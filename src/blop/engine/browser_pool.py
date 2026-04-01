from __future__ import annotations

import asyncio
import json
import os
from dataclasses import dataclass
from typing import Optional

from playwright.async_api import Browser, BrowserContext, Page, Playwright, async_playwright

from blop.engine.browser import make_browser_profile


def _normalize_storage_state_file(path: str) -> None:
    """Ensure every origin in a storage-state JSON has a ``localStorage`` array.

    browser-use's CDP-based state capture omits ``localStorage`` when empty,
    but Playwright 1.50+ requires it to be an explicit array on ``new_context``
    read-back.  We patch the file in-place (idempotent) so subsequent
    ``new_context(storage_state=path)`` calls succeed.
    """
    try:
        if not os.path.isfile(path):
            return
        with open(path) as _f:
            data = json.load(_f)
        changed = False
        for origin in data.get("origins", []):
            if "localStorage" not in origin:
                origin["localStorage"] = []
                changed = True
        if changed:
            with open(path, "w") as _f:
                json.dump(data, _f)
    except Exception:
        pass


@dataclass
class BrowserLease:
    page: Page
    context: BrowserContext
    browser: Browser
    pool: "BrowserPool"
    browser_key: tuple[bool]

    async def close(self) -> None:
        try:
            await self.page.close()
        except Exception:
            pass
        try:
            await self.context.close()
        except Exception:
            pass


class BrowserPool:
    """Process-wide Playwright browser pool with per-task isolated contexts."""

    def __init__(self) -> None:
        self._lock = asyncio.Lock()
        self._pw: Optional[Playwright] = None
        self._browsers: dict[tuple[bool], Browser] = {}

    async def _ensure_browser(self, *, headless: bool) -> tuple[Browser, tuple[bool]]:
        async with self._lock:
            if self._pw is None:
                self._pw = await async_playwright().start()
            key = (headless,)
            browser = self._browsers.get(key)
            if browser is None or not browser.is_connected():
                profile = make_browser_profile(headless=headless, storage_state=None)
                launch_kwargs = {
                    "headless": headless,
                    "args": getattr(profile, "browser_args", []) or [],
                }
                try:
                    browser = await self._pw.chromium.launch(**launch_kwargs)
                except Exception:
                    try:
                        from blop.engine import metrics as blop_metrics

                        blop_metrics.inc_browser_acquire_failure(where="pool_launch")
                    except Exception:
                        pass
                    raise
                self._browsers[key] = browser
            return browser, key

    async def acquire(
        self,
        *,
        headless: bool,
        storage_state: str | None = None,
        accept_downloads: bool = True,
        ignore_https_errors: bool = True,
        record_video_dir: str | None = None,
        record_video_size: dict | None = None,
    ) -> BrowserLease:
        browser, key = await self._ensure_browser(headless=headless)
        context_kwargs: dict = {
            "accept_downloads": accept_downloads,
            "ignore_https_errors": ignore_https_errors,
        }
        if storage_state:
            _normalize_storage_state_file(storage_state)
            context_kwargs["storage_state"] = storage_state
        if record_video_dir:
            context_kwargs["record_video_dir"] = record_video_dir
        if record_video_size:
            context_kwargs["record_video_size"] = record_video_size
        context = await browser.new_context(**context_kwargs)
        page = await context.new_page()
        return BrowserLease(
            page=page,
            context=context,
            browser=browser,
            pool=self,
            browser_key=key,
        )

    async def close(self) -> None:
        async with self._lock:
            for browser in self._browsers.values():
                try:
                    await browser.close()
                except Exception:
                    pass
            self._browsers.clear()
            if self._pw is not None:
                try:
                    await self._pw.stop()
                except Exception:
                    pass
                self._pw = None


BROWSER_POOL = BrowserPool()
