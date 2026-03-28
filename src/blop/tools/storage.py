"""Canonical storage tools with scope-aware compatibility wrappers."""

from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Any, Literal, Optional
from urllib.parse import urlparse

from blop.engine.browser_runtime import acquire_page_session
from blop.engine.browser_session_manager import SESSION_MANAGER
from blop.engine.path_safety import resolve_within_base

# Safe directory for storage_import — must resolve within .blop/
_BLOP_SAFE_DIR = Path(__file__).parent.parent.parent.parent / ".blop"

StorageScope = Literal["profile_url", "compat_session", "regression_replay"]
StorageResource = Literal["cookies", "local_storage", "session_storage", "all"]
StorageSetOperation = Literal["upsert", "delete", "clear"]

_SESSION_HEADLESS = True
_SESSION_TIMEOUT_MS = 30000
_SESSION_POST_NAV_WAIT_MS = 1000
_SESSION_ALLOW_AUTO_ENV = False


def _storage_state_path_for_profile(profile_name: Optional[str]) -> str:
    """Return a sanitized, deterministic storage_state path for a profile."""
    state_dir = Path(__file__).parent.parent.parent.parent / ".blop" / "states"
    state_dir.mkdir(parents=True, exist_ok=True)
    normalized = (profile_name or "default").replace("\\", "/")
    safe_name = os.path.basename(normalized)
    safe_name = re.sub(r"[^A-Za-z0-9_.-]", "_", safe_name) or "default"
    filename = state_dir / f"storage_state_{safe_name}.json"
    if filename.resolve().parent != state_dir.resolve():
        filename = state_dir / "storage_state_default.json"
    return str(filename)


def _storage_error(message: str, **details: Any) -> dict:
    payload = {"error": message}
    payload.update(details)
    return payload


def _resolve_output_path(filename: Optional[str], profile_name: Optional[str]) -> str:
    if filename:
        return filename
    return _storage_state_path_for_profile(profile_name)


def _normalize_cookie(cookie: dict, include_values: bool) -> dict:
    out = {
        "name": cookie.get("name", ""),
        "domain": cookie.get("domain", ""),
        "path": cookie.get("path", "/"),
        "secure": cookie.get("secure", False),
        "httpOnly": cookie.get("httpOnly", False),
        "expires": cookie.get("expires", -1),
        "sameSite": cookie.get("sameSite", ""),
    }
    if include_values:
        out["value"] = cookie.get("value", "")
    return out


async def _profile_url_get(
    resource: StorageResource,
    app_url: Optional[str],
    profile_name: Optional[str],
    name: Optional[str],
    key: Optional[str],
    domain: Optional[str],
    path: Optional[str],
    include_values: bool,
) -> dict:
    if not app_url:
        return _storage_error("app_url is required for scope='profile_url'")
    session = None
    try:
        session = await acquire_page_session(
            app_url,
            profile_name=profile_name,
            headless=_SESSION_HEADLESS,
            timeout_ms=_SESSION_TIMEOUT_MS,
            post_nav_wait_ms=_SESSION_POST_NAV_WAIT_MS,
            allow_auto_env=_SESSION_ALLOW_AUTO_ENV,
        )
        context = session.context
        page = session.page
        cookies = await context.cookies()
        if domain:
            cookies = [c for c in cookies if domain in c.get("domain", "")]
        if path:
            cookies = [c for c in cookies if c.get("path") == path]
        if name:
            cookies = [c for c in cookies if c.get("name") == name]
        cookie_payload = [_normalize_cookie(c, include_values=include_values) for c in cookies]

        if resource == "cookies":
            return {
                "scope": "profile_url",
                "resource": "cookies",
                "count": len(cookie_payload),
                "cookies": cookie_payload,
            }

        local_items = await page.evaluate("() => Object.entries(localStorage)")
        session_items = await page.evaluate("() => Object.entries(sessionStorage)")
        local_payload = [{"key": k, "value": v} for k, v in local_items]
        session_payload = [{"key": k, "value": v} for k, v in session_items]
        if key is not None:
            local_payload = [item for item in local_payload if item["key"] == key]
            session_payload = [item for item in session_payload if item["key"] == key]

        if resource == "local_storage":
            return {
                "scope": "profile_url",
                "resource": "local_storage",
                "count": len(local_payload),
                "items": local_payload,
            }
        if resource == "session_storage":
            return {
                "scope": "profile_url",
                "resource": "session_storage",
                "count": len(session_payload),
                "items": session_payload,
            }
        return {
            "scope": "profile_url",
            "resource": "all",
            "cookies": {"count": len(cookie_payload), "items": cookie_payload},
            "local_storage": {"count": len(local_payload), "items": local_payload},
            "session_storage": {"count": len(session_payload), "items": session_payload},
        }
    except Exception as e:
        return {"error": str(e)}
    finally:
        if session:
            await session.close()


async def _profile_url_set(
    resource: StorageResource,
    operation: StorageSetOperation,
    app_url: Optional[str],
    profile_name: Optional[str],
    cookie: Optional[dict[str, Any]],
    key: Optional[str],
    value: Optional[str],
    name: Optional[str],
    domain: Optional[str],
    path: Optional[str],
    persist: bool,
) -> dict:
    if not app_url:
        return _storage_error("app_url is required for scope='profile_url'")
    session = None
    try:
        session = await acquire_page_session(
            app_url,
            profile_name=profile_name,
            headless=_SESSION_HEADLESS,
            timeout_ms=_SESSION_TIMEOUT_MS,
            post_nav_wait_ms=_SESSION_POST_NAV_WAIT_MS,
            allow_auto_env=_SESSION_ALLOW_AUTO_ENV,
        )
        context = session.context
        page = session.page

        if resource == "cookies":
            if operation == "upsert":
                cookie_input = dict(cookie or {})
                cookie_name = cookie_input.get("name") or name
                cookie_value = cookie_input.get("value")
                cookie_path = cookie_input.get("path") or path or "/"
                if cookie_name is None or cookie_value is None:
                    return _storage_error("cookies upsert requires name and value")
                for bool_field in ("secure", "httpOnly"):
                    val = cookie_input.get(bool_field)
                    if val is not None and not isinstance(val, bool):
                        return _storage_error(
                            f"Cookie '{bool_field}' must be boolean, got {type(val).__name__}: {val!r}"
                        )
                cookie_domain = cookie_input.get("domain") or domain or urlparse(app_url).hostname or ""
                cookie_payload = {
                    "name": cookie_name,
                    "value": cookie_value,
                    "domain": cookie_domain,
                    "path": cookie_path,
                    "secure": bool(cookie_input.get("secure", False)),
                    "httpOnly": bool(cookie_input.get("httpOnly", False)),
                    "url": app_url,
                }
                if "expires" in cookie_input:
                    cookie_payload["expires"] = cookie_input["expires"]
                if "sameSite" in cookie_input:
                    cookie_payload["sameSite"] = cookie_input["sameSite"]
                await context.add_cookies([cookie_payload])
                result = {
                    "status": "set",
                    "resource": "cookies",
                    "name": cookie_name,
                    "domain": cookie_domain,
                    "path": cookie_path,
                }
            elif operation == "delete":
                target_name = name or (cookie or {}).get("name")
                if not target_name:
                    return _storage_error("cookies delete requires name")
                cookies = await context.cookies()
                keep = [c for c in cookies if c.get("name") != target_name]
                await context.clear_cookies()
                if keep:
                    await context.add_cookies(keep)
                result = {"status": "deleted", "resource": "cookies", "name": target_name}
            elif operation == "clear":
                await context.clear_cookies()
                result = {"status": "cleared", "resource": "cookies"}
            else:
                return _storage_error(f"Unsupported operation '{operation}' for cookies")
        elif resource in {"local_storage", "session_storage"}:
            storage_name = "localStorage" if resource == "local_storage" else "sessionStorage"
            if operation == "upsert":
                if key is None or value is None:
                    return _storage_error(f"{resource} upsert requires key and value")
                await page.evaluate(f"([k,v]) => {storage_name}.setItem(k,v)", [key, value])
                result = {"status": "set", "resource": resource, "key": key}
            elif operation == "delete":
                if key is None:
                    return _storage_error(f"{resource} delete requires key")
                await page.evaluate(f"(k) => {storage_name}.removeItem(k)", key)
                result = {"status": "deleted", "resource": resource, "key": key}
            elif operation == "clear":
                await page.evaluate(f"() => {storage_name}.clear()")
                result = {"status": "cleared", "resource": resource}
            else:
                return _storage_error(f"Unsupported operation '{operation}' for {resource}")
        elif resource == "all":
            if operation != "clear":
                return _storage_error("resource='all' currently supports operation='clear' only")
            await context.clear_cookies()
            await page.evaluate("() => localStorage.clear()")
            await page.evaluate("() => sessionStorage.clear()")
            result = {"status": "cleared", "resource": "all"}
        else:
            return _storage_error(f"Unsupported resource '{resource}'")

        storage_state_path = _storage_state_path_for_profile(profile_name)
        if persist:
            await context.storage_state(path=storage_state_path)
            result["persisted"] = True
            result["storage_state_path"] = storage_state_path
        else:
            result["persisted"] = False
        return result
    except Exception as e:
        return {"error": str(e)}
    finally:
        if session:
            await session.close()


async def storage_get(
    scope: StorageScope,
    resource: StorageResource = "cookies",
    app_url: Optional[str] = None,
    profile_name: Optional[str] = None,
    run_id: Optional[str] = None,
    name: Optional[str] = None,
    key: Optional[str] = None,
    domain: Optional[str] = None,
    path: Optional[str] = None,
    include_values: bool = False,
) -> dict:
    """Get scoped cookie/storage values from profile or compat browser context."""
    if scope == "profile_url":
        return await _profile_url_get(
            resource=resource,
            app_url=app_url,
            profile_name=profile_name,
            name=name,
            key=key,
            domain=domain,
            path=path,
            include_values=include_values,
        )
    if scope == "compat_session":
        try:
            if resource == "cookies":
                if name:
                    cookie_result = await SESSION_MANAGER.cookie_get(name=name)
                    cookie = cookie_result.get("cookie")
                    cookies = [] if cookie is None else [cookie]
                else:
                    cookies_result = await SESSION_MANAGER.cookie_list(domain=domain, path=path)
                    cookies = list(cookies_result.get("cookies", []))
                payload = [_normalize_cookie(c, include_values=include_values) for c in cookies]
                return {"scope": scope, "resource": resource, "count": len(payload), "cookies": payload}
            if resource == "local_storage":
                if key is None:
                    return await SESSION_MANAGER.localstorage_list()
                return await SESSION_MANAGER.localstorage_get(key=key)
            if resource == "session_storage":
                if key is None:
                    return await SESSION_MANAGER.sessionstorage_list()
                return await SESSION_MANAGER.sessionstorage_get(key=key)
            if resource == "all":
                cookie_payload = await storage_get(scope=scope, resource="cookies", include_values=include_values)
                local_payload = await storage_get(scope=scope, resource="local_storage")
                session_payload = await storage_get(scope=scope, resource="session_storage")
                return {
                    "scope": scope,
                    "resource": "all",
                    "cookies": cookie_payload,
                    "local_storage": local_payload,
                    "session_storage": session_payload,
                }
            return _storage_error(f"Unsupported resource '{resource}'")
        except Exception as e:
            return {"error": str(e)}
    if scope == "regression_replay":
        return _storage_error(
            "scope='regression_replay' does not expose runtime storage introspection yet",
            scope=scope,
            resource=resource,
            run_id=run_id,
        )
    return _storage_error(f"Unsupported scope '{scope}'")


async def storage_set(
    scope: StorageScope,
    resource: StorageResource = "cookies",
    operation: StorageSetOperation = "upsert",
    app_url: Optional[str] = None,
    profile_name: Optional[str] = None,
    run_id: Optional[str] = None,
    cookie: Optional[dict[str, Any]] = None,
    key: Optional[str] = None,
    value: Optional[str] = None,
    name: Optional[str] = None,
    domain: Optional[str] = None,
    path: Optional[str] = None,
    persist: bool = True,
) -> dict:
    """Mutate scoped cookie/storage values in profile or compat session contexts."""
    if scope == "profile_url":
        return await _profile_url_set(
            resource=resource,
            operation=operation,
            app_url=app_url,
            profile_name=profile_name,
            cookie=cookie,
            key=key,
            value=value,
            name=name,
            domain=domain,
            path=path,
            persist=persist,
        )
    if scope == "compat_session":
        try:
            if resource == "cookies":
                if operation == "upsert":
                    cookie_input = dict(cookie or {})
                    cookie_name = cookie_input.get("name") or name
                    cookie_value = cookie_input.get("value")
                    if cookie_name is None or cookie_value is None:
                        return _storage_error("cookies upsert requires name and value")
                    return await SESSION_MANAGER.cookie_set(
                        name=cookie_name,
                        value=cookie_value,
                        domain=cookie_input.get("domain") or domain,
                        path=cookie_input.get("path") or path or "/",
                        expires=cookie_input.get("expires"),
                        http_only=bool(cookie_input.get("httpOnly", False)),
                        secure=bool(cookie_input.get("secure", False)),
                        same_site=cookie_input.get("sameSite"),
                    )
                if operation == "delete":
                    cookie_name = name or (cookie or {}).get("name")
                    if not cookie_name:
                        return _storage_error("cookies delete requires name")
                    return await SESSION_MANAGER.cookie_delete(name=cookie_name)
                if operation == "clear":
                    return await SESSION_MANAGER.cookie_clear()
                return _storage_error(f"Unsupported operation '{operation}' for cookies")
            if resource == "local_storage":
                if operation == "upsert":
                    if key is None or value is None:
                        return _storage_error("local_storage upsert requires key and value")
                    return await SESSION_MANAGER.localstorage_set(key=key, value=value)
                if operation == "delete":
                    if key is None:
                        return _storage_error("local_storage delete requires key")
                    return await SESSION_MANAGER.localstorage_delete(key=key)
                if operation == "clear":
                    return await SESSION_MANAGER.localstorage_clear()
                return _storage_error(f"Unsupported operation '{operation}' for local_storage")
            if resource == "session_storage":
                if operation == "upsert":
                    if key is None or value is None:
                        return _storage_error("session_storage upsert requires key and value")
                    return await SESSION_MANAGER.sessionstorage_set(key=key, value=value)
                if operation == "delete":
                    if key is None:
                        return _storage_error("session_storage delete requires key")
                    return await SESSION_MANAGER.sessionstorage_delete(key=key)
                if operation == "clear":
                    return await SESSION_MANAGER.sessionstorage_clear()
                return _storage_error(f"Unsupported operation '{operation}' for session_storage")
            if resource == "all":
                if operation != "clear":
                    return _storage_error("resource='all' currently supports operation='clear' only")
                await SESSION_MANAGER.cookie_clear()
                await SESSION_MANAGER.localstorage_clear()
                await SESSION_MANAGER.sessionstorage_clear()
                return {"status": "ok", "resource": "all", "operation": "clear"}
            return _storage_error(f"Unsupported resource '{resource}'")
        except Exception as e:
            return {"error": str(e)}
    if scope == "regression_replay":
        return _storage_error(
            "scope='regression_replay' does not expose runtime storage mutation yet",
            scope=scope,
            resource=resource,
            run_id=run_id,
        )
    return _storage_error(f"Unsupported scope '{scope}'")


async def storage_export(
    scope: StorageScope,
    app_url: Optional[str] = None,
    profile_name: Optional[str] = None,
    run_id: Optional[str] = None,
    filename: Optional[str] = None,
    include_cookies: bool = True,
    include_local_storage: bool = True,
    include_session_storage: bool = True,
) -> dict:
    """Export scoped storage state to JSON."""
    if scope == "compat_session":
        return await SESSION_MANAGER.storage_state_save(filename=filename)
    if scope == "profile_url":
        if not app_url:
            return _storage_error("app_url is required for scope='profile_url'")
        session = None
        try:
            session = await acquire_page_session(
                app_url,
                profile_name=profile_name,
                headless=_SESSION_HEADLESS,
                timeout_ms=_SESSION_TIMEOUT_MS,
                post_nav_wait_ms=_SESSION_POST_NAV_WAIT_MS,
                allow_auto_env=_SESSION_ALLOW_AUTO_ENV,
            )
            output_path = _resolve_output_path(filename=filename, profile_name=profile_name)
            state = await session.context.storage_state()
            if not include_cookies:
                state["cookies"] = []
            if not include_local_storage and not include_session_storage:
                state["origins"] = []
            elif not include_local_storage:
                for origin in state.get("origins", []):
                    origin["localStorage"] = []
            Path(output_path).write_text(json.dumps(state, indent=2))
            return {"status": "saved", "scope": scope, "path": output_path, "app_url": app_url}
        except Exception as e:
            return {"error": str(e)}
        finally:
            if session:
                await session.close()
    return _storage_error(
        "storage export is currently supported only for profile_url and compat_session", scope=scope, run_id=run_id
    )


async def storage_import(
    scope: StorageScope,
    filename: str,
    app_url: Optional[str] = None,
    profile_name: Optional[str] = None,
    run_id: Optional[str] = None,
    merge: bool = False,
) -> dict:
    """Import scoped storage state from JSON."""
    if scope == "compat_session":
        if merge:
            return _storage_error("merge=True is not supported for scope='compat_session'")
        return await SESSION_MANAGER.storage_state_restore(filename=filename)
    if scope == "profile_url":
        if not app_url:
            return _storage_error("app_url is required for scope='profile_url'")
        fp = resolve_within_base(filename, base_dir=_BLOP_SAFE_DIR, must_exist=True, allow_absolute_outside_base=False)
        if fp is None:
            return _storage_error(f"Path must resolve within .blop directory. Got: {filename!r}")
        session = None
        try:
            payload = json.loads(fp.read_text())
            session = await acquire_page_session(
                app_url,
                profile_name=profile_name,
                headless=_SESSION_HEADLESS,
                timeout_ms=_SESSION_TIMEOUT_MS,
                post_nav_wait_ms=_SESSION_POST_NAV_WAIT_MS,
                allow_auto_env=_SESSION_ALLOW_AUTO_ENV,
            )
            context = session.context
            page = session.page
            if not merge:
                await context.clear_cookies()
            cookies = payload.get("cookies", [])
            if cookies:
                await context.add_cookies(cookies)
            for origin in payload.get("origins", []):
                origin_url = origin.get("origin")
                if not origin_url:
                    continue
                try:
                    await page.goto(origin_url, wait_until="domcontentloaded")
                    if not merge:
                        await page.evaluate("() => { localStorage.clear(); sessionStorage.clear(); }")
                    for item in origin.get("localStorage", []):
                        k = item.get("name")
                        v = item.get("value")
                        if k is not None and v is not None:
                            await page.evaluate("({k,v}) => localStorage.setItem(k,v)", {"k": k, "v": v})
                except Exception:
                    continue
            return {"status": "imported", "scope": scope, "path": str(fp), "merge": merge}
        except Exception as e:
            return {"error": str(e)}
        finally:
            if session:
                await session.close()
    return _storage_error(
        "storage import is currently supported only for profile_url and compat_session", scope=scope, run_id=run_id
    )


async def get_browser_cookies(app_url: str, profile_name: Optional[str] = None) -> dict:
    """Legacy wrapper for URL-scoped cookies."""
    result = await storage_get(
        scope="profile_url",
        resource="cookies",
        app_url=app_url,
        profile_name=profile_name,
        include_values=False,
    )
    if "error" in result:
        return result
    return {
        "app_url": app_url,
        "cookie_count": result.get("count", 0),
        "cookies": result.get("cookies", []),
    }


async def set_browser_cookie(
    app_url: str,
    name: str,
    value: str,
    domain: Optional[str] = None,
    path: str = "/",
    secure: bool = False,
    http_only: bool = False,
    profile_name: Optional[str] = None,
) -> dict:
    """Legacy wrapper for URL-scoped cookie set."""
    result = await storage_set(
        scope="profile_url",
        resource="cookies",
        operation="upsert",
        app_url=app_url,
        profile_name=profile_name,
        cookie={
            "name": name,
            "value": value,
            "domain": domain,
            "path": path,
            "secure": secure,
            "httpOnly": http_only,
        },
        persist=True,
    )
    if "error" in result:
        return result
    return {
        "status": "set",
        "name": result.get("name", name),
        "domain": result.get("domain", domain or ""),
        "path": result.get("path", path),
        "persisted": result.get("persisted", False),
        "storage_state_path": result.get("storage_state_path"),
    }


async def save_browser_state(
    app_url: str,
    profile_name: Optional[str] = None,
    filename: Optional[str] = None,
) -> dict:
    """Legacy wrapper for URL-scoped storage export."""
    result = await storage_export(
        scope="profile_url",
        app_url=app_url,
        profile_name=profile_name,
        filename=filename,
    )
    if "error" in result:
        return result
    return {
        "status": "saved",
        "path": result.get("path"),
        "app_url": app_url,
    }
