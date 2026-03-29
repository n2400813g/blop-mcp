"""capture_auth_session tool — interactive OAuth capture via headed browser."""

from __future__ import annotations

import asyncio
import os
from pathlib import Path
from typing import Optional

from blop.config import validate_app_url
from blop.engine.errors import (
    BLOP_AUTH_CAPTURE_TIMEOUT,
    BLOP_URL_VALIDATION_FAILED,
    BLOP_VALIDATION_FAILED,
    tool_error,
)
from blop.engine.path_safety import resolve_within_base, sanitize_component

_BLOP_DIR: str = str(Path(__file__).parent.parent.parent.parent / ".blop")
_REPO_ROOT = Path(__file__).parent.parent.parent.parent.resolve()


async def capture_auth_session(
    profile_name: str,
    login_url: str,
    success_url_pattern: Optional[str] = None,
    timeout_secs: int = 120,
    user_data_dir: Optional[str] = None,
) -> dict:
    """Launch headed browser, wait for user to complete OAuth/MFA, then save storage state.

    Opens a visible browser window at login_url. Polls the URL every 500ms until:
    - The URL matches success_url_pattern (if provided), or
    - The URL changes away from the login URL (if no pattern given).

    On success, saves Playwright storage state and upserts an auth profile in SQLite.
    """
    try:
        safe_profile_name = sanitize_component(profile_name, field_name="profile_name")
    except ValueError as exc:
        return tool_error(
            str(exc),
            BLOP_VALIDATION_FAILED,
            details={"field": "profile_name", "error_type": "invalid_profile_name"},
            error_type="invalid_profile_name",
            status="error",
            profile_name=profile_name,
            note=str(exc),
        )
    url_err = validate_app_url(login_url)
    if url_err:
        return tool_error(
            url_err,
            BLOP_URL_VALIDATION_FAILED,
            details={"field": "login_url", "error_type": "invalid_login_url"},
            error_type="invalid_login_url",
            status="error",
            profile_name=profile_name,
            note=url_err,
        )

    from urllib.parse import urlparse

    from playwright.async_api import async_playwright

    from blop.schemas import AuthProfile
    from blop.storage import sqlite

    os.makedirs(_BLOP_DIR, exist_ok=True)
    state_path = os.path.join(_BLOP_DIR, f"auth_state_{safe_profile_name}.json")
    login_domain = urlparse(login_url).netloc  # e.g. "app.rendley.com"

    captured = False

    async with async_playwright() as p:
        if user_data_dir:
            resolved_user_data_dir = resolve_within_base(
                user_data_dir,
                base_dir=_REPO_ROOT,
                must_exist=False,
            )
            if resolved_user_data_dir is None:
                return tool_error(
                    "user_data_dir must resolve within the repository root",
                    BLOP_VALIDATION_FAILED,
                    details={"field": "user_data_dir", "error_type": "invalid_user_data_dir"},
                    error_type="invalid_user_data_dir",
                    status="error",
                    profile_name=profile_name,
                    note="user_data_dir must resolve within the repository root",
                )
            os.makedirs(resolved_user_data_dir, exist_ok=True)
            context = await p.chromium.launch_persistent_context(
                str(resolved_user_data_dir),
                headless=False,
            )
            browser = None
        else:
            browser = await p.chromium.launch(headless=False)
            context = await browser.new_context()

        try:
            page = await context.new_page()
            await page.goto(login_url, wait_until="networkidle", timeout=15000)
            # Record the settled URL (accounts for redirects like /login → /login)
            settled_login_url = page.url.rstrip("/")

            deadline = asyncio.get_event_loop().time() + timeout_secs
            while asyncio.get_event_loop().time() < deadline:
                await asyncio.sleep(0.5)
                try:
                    current_url = page.url
                    current_domain = urlparse(current_url).netloc
                    # Only consider URLs back on the original app domain — ignore
                    # external OAuth provider pages (accounts.google.com, github.com, etc.)
                    on_app_domain = current_domain == login_domain or current_domain.endswith("." + login_domain)
                    if success_url_pattern:
                        if success_url_pattern in current_url and on_app_domain:
                            captured = True
                            break
                    else:
                        # URL changed AND we're back on the app domain (not mid-OAuth)
                        if on_app_domain and current_url.rstrip("/") != settled_login_url:
                            captured = True
                            break
                except Exception:
                    pass

            if captured:
                # Wait for the auth callback to fully settle (session cookies are set
                # after the OAuth callback handler runs — don't capture mid-redirect)
                try:
                    await page.wait_for_load_state("networkidle", timeout=10000)
                except Exception:
                    pass
                await context.storage_state(path=state_path)
                # Sanitize: Playwright occasionally saves localStorage as undefined
                # for origins that use it; Playwright's restore path requires an array.
                try:
                    import json as _json

                    with open(state_path) as _f:
                        _state = _json.load(_f)
                    for _origin in _state.get("origins", []):
                        if not isinstance(_origin.get("localStorage"), list):
                            _origin["localStorage"] = []
                    with open(state_path, "w") as _f:
                        _json.dump(_state, _f)
                except Exception:
                    pass
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

    if not captured:
        _msg = "No successful login detected within timeout."
        _note = "No successful login detected within timeout. Check success_url_pattern or increase timeout_secs."
        return tool_error(
            _msg,
            BLOP_AUTH_CAPTURE_TIMEOUT,
            details={"error_type": "auth_capture_timeout"},
            status="timeout",
            profile_name=profile_name,
            note=_note,
            error_type="auth_capture_timeout",
        )

    # Upsert auth profile in SQLite as storage_state type
    profile = AuthProfile(
        profile_name=safe_profile_name,
        auth_type="storage_state",
        storage_state_path=state_path,
        user_data_dir=str(resolved_user_data_dir) if user_data_dir else None,
    )
    await sqlite.save_auth_profile(profile, state_path)

    return {
        "status": "captured",
        "profile_name": safe_profile_name,
        "requested_profile_name": profile_name,
        "storage_state_path": state_path,
        "note": "Auth state saved. Pass profile_name to record_test_flow and run_release_check(flow_ids=[...], mode='replay').",
    }
