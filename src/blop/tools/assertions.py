"""Structured assertion tools — lightweight standalone verifications."""
from __future__ import annotations

from typing import Optional


async def _get_page(app_url: str, profile_name: Optional[str] = None):
    """Launch browser and return (page, browser, context, pw) handles.

    Returns:
        page: Playwright page for assertions.
        browser: Playwright browser instance.
        context: Playwright browser context.
        pw: Playwright driver/session handle from async_playwright().start().
    """
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
    await page.wait_for_timeout(1500)
    return page, browser, context, pw


async def verify_element_visible(
    app_url: str,
    role: str,
    accessible_name: str,
    profile_name: Optional[str] = None,
) -> dict:
    """Navigate to app_url and verify that an ARIA element with the given role/name is visible."""
    page = browser = context = pw = None
    try:
        page, browser, context, pw = await _get_page(app_url, profile_name)
        loc = page.get_by_role(role, name=accessible_name)
        count = await loc.count()
        visible = False
        if count > 0:
            try:
                visible = await loc.first.is_visible()
            except Exception:
                pass
        return {
            "assertion": "element_visible",
            "passed": visible,
            "role": role,
            "accessible_name": accessible_name,
            "app_url": app_url,
            "element_count": count,
        }
    except Exception as e:
        return {"assertion": "element_visible", "passed": False, "error": str(e)}
    finally:
        for obj in (context, browser, pw):
            if obj:
                try:
                    await obj.close()
                except Exception:
                    pass


async def verify_text_visible(
    app_url: str,
    text: str,
    profile_name: Optional[str] = None,
) -> dict:
    """Navigate to app_url and verify that the given text is present on the page."""
    page = browser = context = pw = None
    try:
        page, browser, context, pw = await _get_page(app_url, profile_name)
        body_text = await page.inner_text("body")
        found = text in body_text
        return {
            "assertion": "text_visible",
            "passed": found,
            "text": text,
            "app_url": app_url,
        }
    except Exception as e:
        return {"assertion": "text_visible", "passed": False, "error": str(e)}
    finally:
        for obj in (context, browser, pw):
            if obj:
                try:
                    await obj.close()
                except Exception:
                    pass


async def verify_value(
    app_url: str,
    selector: str,
    expected_value: str,
    profile_name: Optional[str] = None,
) -> dict:
    """Navigate to app_url and verify that a form field matches the expected value."""
    page = browser = context = pw = None
    try:
        page, browser, context, pw = await _get_page(app_url, profile_name)
        el = page.locator(selector)
        count = await el.count()
        if count == 0:
            return {
                "assertion": "verify_value",
                "passed": False,
                "selector": selector,
                "error": "Element not found",
            }
        actual = await el.first.input_value(timeout=5000)
        passed = actual == expected_value
        return {
            "assertion": "verify_value",
            "passed": passed,
            "selector": selector,
            "expected": expected_value,
            "actual": actual,
            "app_url": app_url,
        }
    except Exception as e:
        return {"assertion": "verify_value", "passed": False, "error": str(e)}
    finally:
        for obj in (context, browser, pw):
            if obj:
                try:
                    await obj.close()
                except Exception:
                    pass


async def verify_visual_state(
    app_url: str,
    description: str,
    profile_name: Optional[str] = None,
) -> dict:
    """Navigate to app_url and ask the vision LLM whether a visual condition holds."""
    page = browser = context = pw = None
    try:
        page, browser, context, pw = await _get_page(app_url, profile_name)
        from blop.engine.vision import assert_by_vision

        passed = await assert_by_vision(page, description)
        return {
            "assertion": "visual_state",
            "passed": passed,
            "description": description,
            "app_url": app_url,
        }
    except Exception as e:
        return {"assertion": "visual_state", "passed": False, "error": str(e)}
    finally:
        for obj in (context, browser, pw):
            if obj:
                try:
                    await obj.close()
                except Exception:
                    pass
