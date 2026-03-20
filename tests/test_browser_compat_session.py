from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from blop.engine.browser_session_manager import BrowserSessionManager


class FakePage:
    def __init__(self, url: str = "about:blank", title: str = "Blank") -> None:
        self.url = url
        self._title = title
        self._listeners: dict[str, object] = {}

    def on(self, event: str, handler) -> None:
        self._listeners[event] = handler

    async def goto(self, url: str, wait_until: str = "domcontentloaded", timeout: int = 60000) -> None:
        self.url = url
        self._title = f"Title for {url}"

    async def title(self) -> str:
        return self._title

    async def go_back(self, wait_until: str = "domcontentloaded", timeout: int = 60000) -> None:
        self.url = "https://example.com/back"
        self._title = "Back"

    async def close(self) -> None:
        return None


class FakeContext:
    def __init__(self) -> None:
        self.pages: list[FakePage] = []

    async def new_page(self) -> FakePage:
        page = FakePage()
        self.pages.append(page)
        return page

    async def close(self) -> None:
        return None


class FakeBrowser:
    def __init__(self, context: FakeContext) -> None:
        self._context = context
        self.new_context = AsyncMock(return_value=context)

    async def close(self) -> None:
        return None


class FakePw:
    def __init__(self, browser: FakeBrowser) -> None:
        self.chromium = type("Chromium", (), {})()
        self.chromium.launch = AsyncMock(return_value=browser)

    async def stop(self) -> None:
        return None


class FakePlaywrightFactory:
    def __init__(self, pw: FakePw) -> None:
        self._pw = pw

    async def start(self) -> FakePw:
        return self._pw


@pytest.mark.asyncio
async def test_browser_session_manager_navigate_uses_env_storage_state():
    manager = BrowserSessionManager()
    context = FakeContext()
    browser = FakeBrowser(context)
    pw = FakePw(browser)
    pw_factory = FakePlaywrightFactory(pw)

    with patch("blop.engine.browser_session_manager.async_playwright", return_value=pw_factory):
        with patch(
            "blop.engine.browser_session_manager.auth_engine.auto_storage_state_from_env",
            new=AsyncMock(return_value="/tmp/state.json"),
        ):
            try:
                result = await manager.navigate("https://example.com")
                kwargs = browser.new_context.call_args.kwargs
            finally:
                await manager.close()

    assert result["url"] == "https://example.com"
    assert "Title for https://example.com" in result["title"]
    assert kwargs["storage_state"] == "/tmp/state.json"


@pytest.mark.asyncio
async def test_browser_session_manager_navigate_rejects_invalid_url():
    manager = BrowserSessionManager()
    with pytest.raises(ValueError, match="http or https"):
        await manager.navigate("file:///etc/passwd")


@pytest.mark.asyncio
async def test_browser_session_manager_tabs_list_and_select():
    manager = BrowserSessionManager()
    context = FakeContext()
    browser = FakeBrowser(context)
    pw = FakePw(browser)
    pw_factory = FakePlaywrightFactory(pw)

    with patch("blop.engine.browser_session_manager.async_playwright", return_value=pw_factory):
        with patch(
            "blop.engine.browser_session_manager.auth_engine.auto_storage_state_from_env",
            new=AsyncMock(return_value=None),
        ):
            try:
                await manager.ensure_started()
                await manager.navigate("https://example.com")
                await manager.tabs("new")
                listed = await manager.tabs("list")
                selected = await manager.tabs("select", index=1)
            finally:
                await manager.close()

    assert len(listed["tabs"]) == 2
    assert selected["status"] == "selected"
    assert selected["index"] == 1


@pytest.mark.asyncio
async def test_browser_compat_wrapper_delegates_to_session_manager():
    from blop.tools import browser_compat

    with patch.object(browser_compat.SESSION_MANAGER, "navigate", new=AsyncMock(return_value={"url": "https://example.com"})) as nav:
        result = await browser_compat.browser_navigate("https://example.com", profile_name="staging")

    nav.assert_awaited_once_with("https://example.com", profile_name="staging")
    assert result["url"] == "https://example.com"


@pytest.mark.asyncio
async def test_server_browser_network_requests_maps_include_static():
    from blop import server

    with patch("blop.server.capability_flags.is_tool_enabled", return_value=True):
        with patch(
            "blop.server.browser_compat.browser_network_requests",
            new=AsyncMock(return_value={"ok": True}),
        ) as handler:
            result = await server.browser_network_requests(includeStatic=True)

    handler.assert_awaited_once_with(include_static=True)
    assert result == {"ok": True}


@pytest.mark.asyncio
async def test_server_browser_take_screenshot_maps_fullpage_and_type():
    from blop import server

    with patch("blop.server.capability_flags.is_tool_enabled", return_value=True):
        with patch(
            "blop.server.browser_compat.browser_take_screenshot",
            new=AsyncMock(return_value={"ok": True}),
        ) as handler:
            result = await server.browser_take_screenshot(
                filename="shot.png",
                fullPage=True,
                ref="1",
                selector=None,
                type="jpeg",
            )

    handler.assert_awaited_once_with(
        filename="shot.png",
        full_page=True,
        ref="1",
        selector=None,
        img_type="jpeg",
    )
    assert result == {"ok": True}


@pytest.mark.asyncio
async def test_server_browser_cookie_set_maps_http_only_same_site_and_secure_default():
    from blop import server

    with patch("blop.server.capability_flags.is_tool_enabled", return_value=True):
        with patch(
            "blop.server.browser_compat.browser_cookie_set",
            new=AsyncMock(return_value={"ok": True}),
        ) as handler:
            result = await server.browser_cookie_set(
                name="sid",
                value="abc",
                httpOnly=True,
                sameSite="Lax",
            )

    handler.assert_awaited_once_with(
        name="sid",
        value="abc",
        domain=None,
        path="/",
        expires=None,
        http_only=True,
        secure=True,
        same_site="Lax",
    )
    assert result["ok"] is True
    assert result["deprecation_notice"]["replacement_tool"] == "storage_set"


@pytest.mark.asyncio
async def test_server_safe_compat_call_returns_blocked_when_capability_disabled():
    from blop import server

    with patch("blop.server.capability_flags.is_tool_enabled", return_value=False):
        result = await server.browser_hover(ref="12")

    assert "error" in result
    assert "disabled by capabilities" in result["error"]


@pytest.mark.asyncio
async def test_server_safe_compat_call_returns_error_envelope_on_handler_exception():
    from blop import server

    with patch("blop.server.capability_flags.is_tool_enabled", return_value=True):
        with patch(
            "blop.server.browser_compat.browser_hover",
            new=AsyncMock(side_effect=RuntimeError("boom")),
        ):
            result = await server.browser_hover(ref="12")

    assert "internal error" in result["error"]
    assert result["error_type"] == "RuntimeError"
    assert result["tool"] == "browser_hover"


def test_browser_session_manager_resolve_output_path_blocks_traversal():
    manager = BrowserSessionManager()
    with pytest.raises(ValueError):
        manager._resolve_output_path("../escape.png", default_ext=".png")


def test_browser_session_manager_resolve_output_path_blocks_absolute_path():
    manager = BrowserSessionManager()
    with pytest.raises(ValueError):
        manager._resolve_output_path("/tmp/escape.png", default_ext=".png")
