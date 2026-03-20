"""Regression tests pinning the fix for the always-true assertion in test_auth.py.

These tests verify that the cookie_json path in resolve_storage_state actually
invokes Playwright correctly and handles error cases properly.
"""
from __future__ import annotations

import pytest
from unittest.mock import AsyncMock, patch

from blop.schemas import AuthProfile


def _make_cookie_profile(tmp_path, content: str = "[]") -> AuthProfile:
    cookie_file = tmp_path / "cookies.json"
    cookie_file.write_text(content)
    return AuthProfile(
        profile_name="cookie_profile",
        auth_type="cookie_json",
        cookie_json_path=str(cookie_file),
    )


def _make_playwright_mocks():
    mock_context = AsyncMock()
    mock_browser = AsyncMock()
    mock_browser.new_context.return_value = mock_context
    mock_playwright = AsyncMock()
    mock_playwright.__aenter__ = AsyncMock(return_value=mock_playwright)
    mock_playwright.__aexit__ = AsyncMock(return_value=False)
    mock_playwright.chromium.launch.return_value = mock_browser
    return mock_playwright, mock_browser, mock_context


@pytest.mark.asyncio
async def test_cookie_json_path_calls_playwright_context(tmp_path):
    """cookie_json auth must call browser.new_context() exactly once."""
    import blop.engine.auth as auth_engine
    from blop.engine.auth import resolve_storage_state

    profile = _make_cookie_profile(tmp_path)
    mock_playwright, mock_browser, mock_context = _make_playwright_mocks()

    # Allow absolute paths outside repo root so tmp_path is accepted
    with patch.object(auth_engine, "_ALLOW_ABSOLUTE_AUTH_PATHS", True):
        with patch("playwright.async_api.async_playwright", return_value=mock_playwright):
            with patch("os.makedirs"):
                await resolve_storage_state(profile)

    mock_browser.new_context.assert_called_once()


@pytest.mark.asyncio
async def test_cookie_json_path_attempts_add_cookies(tmp_path):
    """cookie_json auth must call context.add_cookies() with the parsed cookie list."""
    import json
    import blop.engine.auth as auth_engine
    from blop.engine.auth import resolve_storage_state

    cookies = [{"name": "session", "value": "abc", "domain": "example.com", "path": "/"}]
    profile = _make_cookie_profile(tmp_path, json.dumps(cookies))
    mock_playwright, mock_browser, mock_context = _make_playwright_mocks()

    with patch.object(auth_engine, "_ALLOW_ABSOLUTE_AUTH_PATHS", True):
        with patch("playwright.async_api.async_playwright", return_value=mock_playwright):
            with patch("os.makedirs"):
                await resolve_storage_state(profile)

    mock_context.add_cookies.assert_called_once()
    call_args = mock_context.add_cookies.call_args
    called_with = call_args[0][0] if call_args[0] else call_args[1].get("cookies", [])
    assert called_with == cookies


@pytest.mark.asyncio
async def test_cookie_json_path_returns_none_on_missing_file():
    """A non-existent cookie_json_path must return None (not crash)."""
    from blop.engine.auth import resolve_storage_state

    profile = AuthProfile(
        profile_name="missing_cookie",
        auth_type="cookie_json",
        cookie_json_path="/nonexistent/path/cookies.json",
    )

    result = await resolve_storage_state(profile)
    assert result is None
