"""Shared Playwright page-session lifecycle helpers for tool modules."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from playwright.async_api import Browser, BrowserContext, Page, Playwright


@dataclass
class PageSession:
    page: Page
    context: BrowserContext
    browser: Browser
    pw: Playwright
    storage_state_path: Optional[str] = None

    async def close(self) -> None:
        for obj in (self.context, self.browser):
            if obj:
                try:
                    await obj.close()
                except Exception:
                    pass
        if self.pw:
            try:
                if hasattr(self.pw, "stop"):
                    await self.pw.stop()
                    return
                if hasattr(self.pw, "close"):
                    await self.pw.close()
            except Exception:
                try:
                    if hasattr(self.pw, "close"):
                        await self.pw.close()
                except Exception:
                    pass


async def acquire_page_session(
    app_url: str,
    *,
    profile_name: Optional[str] = None,
    headless: bool = True,
    timeout_ms: int = 30000,
    post_nav_wait_ms: int = 1000,
    allow_auto_env: bool = True,
) -> PageSession:
    """Launch browser, optionally restore auth state, navigate, and return handles."""
    # Import in-function to avoid circular dependencies and defer startup cost until needed.
    from playwright.async_api import async_playwright

    from blop.engine.auth import resolve_storage_state_for_profile

    storage_state = await resolve_storage_state_for_profile(
        profile_name,
        allow_auto_env=allow_auto_env,
    )

    pw: Optional[Playwright] = None
    browser: Optional[Browser] = None
    context: Optional[BrowserContext] = None
    page: Optional[Page] = None
    try:
        pw = await async_playwright().start()
        browser = await pw.chromium.launch(headless=headless)
        ctx_kwargs: dict = {}
        if storage_state:
            ctx_kwargs["storage_state"] = storage_state
        context = await browser.new_context(**ctx_kwargs)
        page = await context.new_page()
        await page.goto(app_url, wait_until="domcontentloaded", timeout=timeout_ms)
        if post_nav_wait_ms > 0:
            await page.wait_for_timeout(post_nav_wait_ms)
        return PageSession(
            page=page,
            context=context,
            browser=browser,
            pw=pw,
            storage_state_path=storage_state,
        )
    except Exception:
        for obj in (page, context, browser):
            if obj:
                try:
                    await obj.close()
                except Exception:
                    pass
        if pw:
            try:
                if hasattr(pw, "stop"):
                    await pw.stop()
                elif hasattr(pw, "close"):
                    await pw.close()
            except Exception:
                pass
        raise
