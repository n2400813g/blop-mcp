"""Granular storage state management tools — cookies, localStorage."""
from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Optional


def _storage_state_path_for_profile(profile_name: Optional[str]) -> str:
    """Return a sanitized, deterministic storage_state path for a profile."""
    state_dir = Path(__file__).parent.parent.parent.parent / ".blop" / "states"
    state_dir.mkdir(parents=True, exist_ok=True)

    normalized = (profile_name or "default").replace("\\", "/")
    safe_name = os.path.basename(normalized)
    safe_name = re.sub(r"[^A-Za-z0-9_.-]", "_", safe_name)
    if not safe_name:
        safe_name = "default"

    filename = state_dir / f"storage_state_{safe_name}.json"
    if filename.resolve().parent != state_dir.resolve():
        filename = state_dir / "storage_state_default.json"
    return str(filename)


async def _get_page_and_context(app_url: str, profile_name: Optional[str] = None):
    """Launch browser, navigate, return (page, context, browser, pw, storage_state_path)."""
    from playwright.async_api import async_playwright
    from blop.engine.auth import resolve_storage_state
    from blop.storage.sqlite import get_auth_profile

    storage_state = None
    if profile_name:
        profile = await get_auth_profile(profile_name)
        if profile:
            try:
                storage_state = await resolve_storage_state(profile)
            except Exception:
                pass

    pw = await async_playwright().start()
    browser = await pw.chromium.launch(headless=True)
    ctx_kwargs: dict = {}
    if storage_state:
        ctx_kwargs["storage_state"] = storage_state
    context = await browser.new_context(**ctx_kwargs)
    page = await context.new_page()
    await page.goto(app_url, wait_until="domcontentloaded", timeout=30000)
    await page.wait_for_timeout(1000)
    return page, context, browser, pw, _storage_state_path_for_profile(profile_name)


async def get_browser_cookies(
    app_url: str,
    profile_name: Optional[str] = None,
) -> dict:
    """Get all cookies for a given app URL."""
    page = context = browser = pw = None
    try:
        page, context, browser, pw, _storage_state_path = await _get_page_and_context(app_url, profile_name)
        cookies = await context.cookies()
        return {
            "app_url": app_url,
            "cookie_count": len(cookies),
            "cookies": [
                {
                    "name": c["name"],
                    "domain": c.get("domain", ""),
                    "path": c.get("path", "/"),
                    "secure": c.get("secure", False),
                    "httpOnly": c.get("httpOnly", False),
                    "expires": c.get("expires", -1),
                    "sameSite": c.get("sameSite", ""),
                }
                for c in cookies
            ],
        }
    except Exception as e:
        return {"error": str(e)}
    finally:
        for obj in (context, browser, pw):
            if obj:
                try:
                    await obj.close()
                except Exception:
                    pass


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
    """Set a specific cookie in the browser context."""
    page = context = browser = pw = None
    try:
        page, context, browser, pw, storage_state_path = await _get_page_and_context(app_url, profile_name)

        from urllib.parse import urlparse
        parsed = urlparse(app_url)
        cookie_domain = domain or parsed.hostname or ""

        await context.add_cookies([{
            "name": name,
            "value": value,
            "domain": cookie_domain,
            "path": path,
            "secure": secure,
            "httpOnly": http_only,
            "url": app_url,
        }])
        await context.storage_state(path=storage_state_path)

        return {
            "status": "set",
            "name": name,
            "domain": cookie_domain,
            "path": path,
            "persisted": True,
            "storage_state_path": storage_state_path,
        }
    except Exception as e:
        return {"error": str(e)}
    finally:
        for obj in (context, browser, pw):
            if obj:
                try:
                    await obj.close()
                except Exception:
                    pass


async def save_browser_state(
    app_url: str,
    profile_name: Optional[str] = None,
    filename: Optional[str] = None,
) -> dict:
    """Save the full browser storage state (cookies + localStorage) to a JSON file."""
    page = context = browser = pw = None
    try:
        page, context, browser, pw, profile_state_path = await _get_page_and_context(app_url, profile_name)

        if not filename:
            filename = profile_state_path

        await context.storage_state(path=filename)

        return {
            "status": "saved",
            "path": filename,
            "app_url": app_url,
        }
    except Exception as e:
        return {"error": str(e)}
    finally:
        for obj in (context, browser, pw):
            if obj:
                try:
                    await obj.close()
                except Exception:
                    pass
