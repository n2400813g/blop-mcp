"""Auth engine for AuthProfile."""
from __future__ import annotations

import asyncio
import json
import os
import time
from pathlib import Path
from typing import Optional

from blop.config import (
    APP_BASE_URL,
    BLOP_AUTH_LOGIN_POLL_INTERVAL_MS,
    BLOP_AUTH_LOGIN_POLL_STEPS,
    BLOP_AUTH_NETWORKIDLE_TIMEOUT_MS,
    BLOP_VALIDATE_AUTH_CACHE,
)
from blop.engine.logger import get_logger
from blop.engine.path_safety import resolve_within_base, sanitize_component
from blop.schemas import AuthProfile

# Anchor .blop/ to the repo root so paths work regardless of the server's CWD.
# auth.py lives at src/blop/engine/auth.py → 4 levels up = repo root.
_BLOP_DIR: str = str(Path(__file__).parent.parent.parent.parent / ".blop")
_REPO_ROOT = Path(__file__).parent.parent.parent.parent.resolve()

_auth_cache: dict[str, dict] = {}
_validated_session_cache: dict[tuple[str, str, int], dict[str, float | bool]] = {}
# Per-profile lock to prevent concurrent logins racing each other
_login_locks: dict[str, asyncio.Lock] = {}
_lock_creation_lock = asyncio.Lock()
_log = get_logger("auth")
_AUTH_VALIDATION_CACHE_TTL_SECS = float(os.getenv("BLOP_AUTH_VALIDATION_CACHE_TTL_SECS", "60"))

# Set to True to allow absolute paths outside the repo root for auth files
_ALLOW_ABSOLUTE_AUTH_PATHS: bool = os.getenv("BLOP_ALLOW_ABSOLUTE_AUTH_PATHS", "").lower() in ("1", "true", "yes")


async def _get_lock(key: str) -> asyncio.Lock:
    if key not in _login_locks:
        async with _lock_creation_lock:
            if key not in _login_locks:
                _login_locks[key] = asyncio.Lock()
    return _login_locks[key]


async def resolve_storage_state(profile: AuthProfile) -> Optional[str]:
    """Return path to a valid Playwright storage_state.json, or None."""
    if profile.auth_type == "env_login":
        return await _env_login(profile)
    elif profile.auth_type == "storage_state":
        return _storage_state(profile)
    elif profile.auth_type == "cookie_json":
        return await _cookie_json(profile)
    return None


async def resolve_storage_state_for_profile(
    profile_name: Optional[str],
    *,
    allow_auto_env: bool = True,
    profile: Optional[AuthProfile] = None,
) -> Optional[str]:
    """Resolve storage state by profile name with optional auto-env fallback.

    This is the canonical helper for tool/orchestration layers so they do not
    duplicate `get_auth_profile` + `resolve_storage_state` + auto-env logic.
    """
    if profile is not None:
        resolved = await resolve_storage_state(profile)
        if resolved is None and allow_auto_env:
            return await auto_storage_state_from_env()
        return resolved

    if not profile_name:
        return await auto_storage_state_from_env() if allow_auto_env else None

    try:
        from blop.storage.sqlite import get_auth_profile

        loaded_profile = await get_auth_profile(profile_name)
        if loaded_profile is None:
            return await auto_storage_state_from_env() if allow_auto_env else None
        resolved = await resolve_storage_state(loaded_profile)
        if resolved is None and allow_auto_env:
            return await auto_storage_state_from_env()
        return resolved
    except Exception as e:
        _log.warning(
            (
                "auth_resolve_failed event=auth_resolve_failed profile_name=%s "
                "allow_auto_env=%s fallback=%s error_type=%s error_message=%s"
            ),
            profile_name,
            allow_auto_env,
            "auto_storage_state_from_env" if allow_auto_env else "none",
            type(e).__name__,
            str(e)[:200],
            exc_info=True,
        )
        return await auto_storage_state_from_env() if allow_auto_env else None


async def _env_login(profile: AuthProfile) -> Optional[str]:
    cache_key = profile.profile_name
    os.makedirs(_BLOP_DIR, exist_ok=True)
    try:
        safe_cache_key = sanitize_component(cache_key, field_name="profile_name")
    except ValueError:
        return None
    state_path = os.path.join(_BLOP_DIR, f"auth_state_{safe_cache_key}.json")

    async def _cached_state_is_usable(path: str) -> bool:
        if not BLOP_VALIDATE_AUTH_CACHE:
            return True
        if not APP_BASE_URL:
            return True
        try:
            return await validate_auth_session(path, APP_BASE_URL)
        except Exception:
            return False

    # Fast path: in-memory cache hit
    entry = _auth_cache.get(cache_key)
    if entry and time.time() < entry["expires"] and os.path.exists(entry["path"]):
        if await _cached_state_is_usable(entry["path"]):
            return entry["path"]
        _log.debug("auth_cache_invalidated profile=%s reason=session_invalid", cache_key)
        _auth_cache.pop(cache_key, None)

    # Serialize concurrent callers — only one login per profile at a time
    async with await _get_lock(cache_key):
        # Re-check after acquiring lock (another coroutine may have just finished)
        entry = _auth_cache.get(cache_key)
        if entry and time.time() < entry["expires"] and os.path.exists(entry["path"]):
            if await _cached_state_is_usable(entry["path"]):
                return entry["path"]
            _log.debug("auth_cache_invalidated profile=%s reason=session_invalid_locked", cache_key)
            _auth_cache.pop(cache_key, None)

        username_env = profile.username_env or "TEST_USERNAME"
        password_env = profile.password_env or "TEST_PASSWORD"
        username = os.getenv(username_env)
        password = os.getenv(password_env)
        from blop.config import LOGIN_URL, TEST_AUTH_URL
        login_url = profile.login_url or LOGIN_URL or TEST_AUTH_URL

        # No credentials — fall back to existing state file if available
        if not (username and password and login_url):
            if os.path.exists(state_path):
                return state_path
            return None

        from playwright.async_api import async_playwright

        username_selector = os.getenv("TEST_USERNAME_SELECTOR", "")
        password_selector = os.getenv("TEST_PASSWORD_SELECTOR", "")

        _user_selectors = [s for s in [username_selector] if s] + [
            "input[name='username']", "input[name='email']",
            "input[type='email']", "#email", "input[placeholder*='email' i]",
            "input[placeholder*='username' i]",
        ]
        _pass_selectors = [s for s in [password_selector] if s] + [
            "input[name='password']", "input[type='password']", "#password",
        ]

        async def _inspect_login_surface(page) -> dict:
            try:
                return await page.evaluate(
                    """() => {
                        const visible = (el) => {
                            if (!el) return false;
                            const style = window.getComputedStyle(el);
                            const rect = el.getBoundingClientRect();
                            return style.visibility !== 'hidden' && style.display !== 'none' && rect.width > 0 && rect.height > 0;
                        };
                        const userInputs = Array.from(document.querySelectorAll(
                            "input[name='username'], input[name='email'], input[type='email'], #email, input[placeholder*='email' i], input[placeholder*='username' i]"
                        )).filter(visible);
                        const passInputs = Array.from(document.querySelectorAll(
                            "input[name='password'], input[type='password'], #password"
                        )).filter(visible);
                        const socialButtons = Array.from(document.querySelectorAll('button, a, [role=\"button\"]'))
                            .map((el) => ((el.innerText || el.textContent || el.getAttribute('aria-label') || '') + '').trim())
                            .filter(Boolean)
                            .filter((text) => /continue with|sign in with|google|microsoft|facebook|github|apple|sso|oauth/i.test(text))
                            .slice(0, 8);
                        return {
                            user_input_count: userInputs.length,
                            password_input_count: passInputs.length,
                            social_buttons: socialButtons,
                            body_text_length: (document.body.innerText || "").trim().length,
                        };
                    }"""
                )
            except Exception:
                return {"user_input_count": 0, "password_input_count": 0, "social_buttons": [], "body_text_length": 0}

        async def _try_fill(page, selectors: list[str], value: str) -> str:
            for sel in selectors:
                try:
                    el = await page.wait_for_selector(sel, timeout=5000)
                    if el:
                        await el.fill(value)
                        return sel
                except Exception as e:
                    _log.debug(
                        "auth_selector_fill_failed profile=%s selector=%s error_type=%s error_message=%s",
                        cache_key,
                        sel,
                        type(e).__name__,
                        str(e)[:160],
                    )
                    continue
            raise RuntimeError(f"Could not find input with any of: {selectors}")

        try:
            async with async_playwright() as p:
                # Use a persistent context when user_data_dir is set — this prevents
                # OAuth providers (Google, GitHub) from detecting a fresh browser context
                # as a bot and blocking automated login.
                if profile.user_data_dir:
                    user_data_dir = resolve_within_base(
                        profile.user_data_dir,
                        base_dir=_REPO_ROOT,
                        must_exist=False,
                    )
                    if user_data_dir is None:
                        raise RuntimeError("user_data_dir must resolve within the repository root")
                    os.makedirs(user_data_dir, exist_ok=True)
                    context = await p.chromium.launch_persistent_context(
                        str(user_data_dir), headless=True,
                    )
                    browser = None
                else:
                    browser = await p.chromium.launch(headless=True)
                    context = await browser.new_context()

                try:
                    page = await context.new_page()
                    await page.goto(login_url, wait_until="domcontentloaded", timeout=15000)
                    surface = await _inspect_login_surface(page)
                    if (
                        surface.get("user_input_count", 0) == 0
                        and surface.get("password_input_count", 0) == 0
                        and not surface.get("social_buttons")
                        and surface.get("body_text_length", 0) == 0
                    ):
                        await page.wait_for_timeout(2500)
                        surface = await _inspect_login_surface(page)
                    if (
                        surface.get("user_input_count", 0) == 0
                        and surface.get("password_input_count", 0) == 0
                        and surface.get("social_buttons")
                    ):
                        _log.info(
                            "env_login_social_only profile=%s login_url=%s buttons=%s",
                            cache_key,
                            login_url,
                            surface.get("social_buttons", []),
                        )
                        if os.path.exists(state_path):
                            return state_path
                        return None
                    await _try_fill(page, _user_selectors, username)
                    working_pass_sel = await _try_fill(page, _pass_selectors, password)
                    await page.press(working_pass_sel, "Enter")

                    # Wait for navigation away from the login page.
                    # Using a URL-polling loop handles multi-step OAuth redirects
                    # (login → IdP → MFA → callback → app) that fool networkidle.
                    _login_path_kws = ("login", "signin", "sign-in", "oauth", "auth/")
                    poll_interval_s = max(BLOP_AUTH_LOGIN_POLL_INTERVAL_MS, 50) / 1000.0
                    for _ in range(max(BLOP_AUTH_LOGIN_POLL_STEPS, 1)):
                        await asyncio.sleep(poll_interval_s)
                        current_url = page.url.lower()
                        if not any(kw in current_url for kw in _login_path_kws):
                            break
                    else:
                        # Fallback: just wait for network to settle
                        try:
                            await page.wait_for_load_state(
                                "networkidle",
                                timeout=max(BLOP_AUTH_NETWORKIDLE_TIMEOUT_MS, 1000),
                            )
                        except Exception:
                            pass

                    # Validate login succeeded: check URL and page text
                    current_url = page.url

                    page_text = ""
                    try:
                        page_text = (await page.evaluate("() => document.body.innerText")).lower()
                    except Exception:
                        pass
                    _login_error_kws = (
                        "invalid", "incorrect", "failed", "wrong password",
                        "no account", "not found", "error signing in",
                    )
                    page_has_error = any(kw in page_text for kw in _login_error_kws)

                    login_failed = (
                        login_url.rstrip("/") in current_url.rstrip("/")
                        or "login" in current_url.lower()
                        or "auth" in current_url.lower()
                        or "signin" in current_url.lower()
                        or page_has_error
                    )
                    if login_failed:
                        if os.path.exists(state_path):
                            return state_path
                        return None

                    await context.storage_state(path=state_path)
                finally:
                    try:
                        await context.close()
                    except Exception:
                        pass
                    if browser:
                        try:
                            await browser.close()
                        except Exception:
                            pass

            # Cache update inside the lock — guaranteed to run only on successful login
            _auth_cache[cache_key] = {"path": state_path, "expires": time.time() + 3600}
            return state_path

        except Exception as e:
            _log.debug(
                "env_login_failed event=env_login_failed profile=%s error_type=%s error_message=%s",
                cache_key,
                type(e).__name__,
                str(e)[:200],
                exc_info=True,
            )
            # Login threw — fall back to existing state file if available
            if os.path.exists(state_path):
                return state_path
            return None


def _storage_state(profile: AuthProfile) -> Optional[str]:
    path = profile.storage_state_path or os.getenv("STORAGE_STATE_PATH")
    resolved = resolve_within_base(
        path or "",
        base_dir=_REPO_ROOT,
        must_exist=True,
        allow_absolute_outside_base=_ALLOW_ABSOLUTE_AUTH_PATHS,
    )
    return str(resolved) if resolved else None


async def validate_auth_session(
    storage_state_path: str,
    app_url: str,
    auth_redirect_patterns: tuple[str, ...] = ("/login", "/signin", "/sign-in", "/auth"),
) -> bool:
    """Open headless browser with storage_state, navigate to app_url, return True if not redirected to auth."""
    try:
        mtime_ns = os.stat(storage_state_path).st_mtime_ns
    except OSError:
        invalidate_validated_session_cache(storage_state_path=storage_state_path, app_url=app_url)
        mtime_ns = -1

    cache_key = (storage_state_path, app_url, mtime_ns)
    now = time.time()
    _prune_validated_session_cache(now=now)
    _drop_stale_validation_entries(storage_state_path, app_url, keep_mtime_ns=mtime_ns)
    entry = _validated_session_cache.get(cache_key)
    if entry and now < float(entry["expires"]):
        return bool(entry["is_valid"])

    is_valid = await _validate_auth_session_uncached(
        storage_state_path=storage_state_path,
        app_url=app_url,
        auth_redirect_patterns=auth_redirect_patterns,
    )
    if is_valid:
        _validated_session_cache[cache_key] = {
            "is_valid": True,
            "expires": now + max(_AUTH_VALIDATION_CACHE_TTL_SECS, 0.0),
        }
    else:
        invalidate_validated_session_cache(storage_state_path=storage_state_path, app_url=app_url)
    return is_valid


async def _validate_auth_session_uncached(
    storage_state_path: str,
    app_url: str,
    auth_redirect_patterns: tuple[str, ...] = ("/login", "/signin", "/sign-in", "/auth"),
) -> bool:
    from playwright.async_api import async_playwright

    try:
        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True)
            context = await browser.new_context(storage_state=storage_state_path)
            page = await context.new_page()
            await page.goto(app_url, wait_until="domcontentloaded", timeout=15000)
            current_url = page.url.lower()
            await context.close()
            await browser.close()
            return not any(pat in current_url for pat in auth_redirect_patterns)
    except Exception as e:
        _log.debug(
            "validate_auth_failed event=validate_auth_failed app_url=%s error_type=%s error_message=%s",
            app_url[:200],
            type(e).__name__,
            str(e)[:200],
            exc_info=True,
        )
        return False


def invalidate_validated_session_cache(
    *,
    storage_state_path: str | None = None,
    app_url: str | None = None,
) -> None:
    keys_to_remove = [
        key
        for key in list(_validated_session_cache.keys())
        if (storage_state_path is None or key[0] == storage_state_path)
        and (app_url is None or key[1] == app_url)
    ]
    for key in keys_to_remove:
        _validated_session_cache.pop(key, None)


def _prune_validated_session_cache(*, now: float | None = None) -> None:
    current_time = time.time() if now is None else now
    keys_to_remove = [
        key for key, entry in list(_validated_session_cache.items())
        if current_time >= float(entry["expires"])
    ]
    for key in keys_to_remove:
        _validated_session_cache.pop(key, None)


def _drop_stale_validation_entries(
    storage_state_path: str,
    app_url: str,
    *,
    keep_mtime_ns: int,
) -> None:
    keys_to_remove = [
        key
        for key in list(_validated_session_cache.keys())
        if key[0] == storage_state_path and key[1] == app_url and key[2] != keep_mtime_ns
    ]
    for key in keys_to_remove:
        _validated_session_cache.pop(key, None)


async def auto_storage_state_from_env() -> Optional[str]:
    """Resolve a storage state from TEST_USERNAME / TEST_PASSWORD / LOGIN_URL env vars.

    Creates an ephemeral AuthProfile with profile_name '_auto_env' so the result lands
    in the shared 1-hour in-memory cache. Returns None if any required credential is absent.
    """
    from blop.config import LOGIN_URL, TEST_AUTH_URL, TEST_USERNAME, TEST_PASSWORD
    if not (TEST_USERNAME and TEST_PASSWORD and (LOGIN_URL or TEST_AUTH_URL)):
        return None
    profile = AuthProfile(
        profile_name="_auto_env",
        auth_type="env_login",
        login_url=LOGIN_URL or TEST_AUTH_URL,
    )
    return await _env_login(profile)


async def _cookie_json(profile: AuthProfile) -> Optional[str]:
    cookie_path = profile.cookie_json_path or os.getenv("COOKIE_JSON_PATH")
    resolved_cookie_path = resolve_within_base(
        cookie_path or "",
        base_dir=_REPO_ROOT,
        must_exist=True,
        allow_absolute_outside_base=_ALLOW_ABSOLUTE_AUTH_PATHS,
    )
    if not resolved_cookie_path:
        return None

    from playwright.async_api import async_playwright

    with open(resolved_cookie_path) as f:
        cookies = json.load(f)

    os.makedirs(_BLOP_DIR, exist_ok=True)
    try:
        safe_profile_name = sanitize_component(profile.profile_name, field_name="profile_name")
    except ValueError:
        return None
    state_path = os.path.join(_BLOP_DIR, f"auth_state_{safe_profile_name}.json")

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        context = await browser.new_context()
        await context.add_cookies(cookies)
        await context.storage_state(path=state_path)
        await browser.close()

    _auth_cache[safe_profile_name] = {"path": state_path, "expires": time.time() + 3600}
    return state_path
