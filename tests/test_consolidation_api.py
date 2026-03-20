from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest


@pytest.mark.asyncio
async def test_storage_get_profile_url_requires_app_url():
    from blop.tools.storage import storage_get

    result = await storage_get(scope="profile_url", resource="cookies")

    assert "error" in result
    assert "app_url is required" in result["error"]


@pytest.mark.asyncio
async def test_storage_set_regression_replay_not_supported_yet():
    from blop.tools.storage import storage_set

    result = await storage_set(
        scope="regression_replay",
        resource="cookies",
        operation="upsert",
        run_id="run-123",
        cookie={"name": "sid", "value": "abc"},
    )

    assert "error" in result
    assert "regression_replay" in result["error"]


@pytest.mark.asyncio
async def test_route_register_and_clear_regression_scope():
    from blop.tools.network import _active_routes, route_clear, route_list, route_register

    _active_routes.clear()
    await route_register(scope="regression_replay", pattern="**/api/a", status=200, name="a")
    await route_register(scope="regression_replay", pattern="**/api/b", status=500, name="b")
    listed = await route_list(scope="regression_replay")
    assert listed["count"] == 2

    cleared = await route_clear(scope="regression_replay", name="a")
    assert cleared["removed_count"] == 1
    listed_after = await route_list(scope="regression_replay")
    assert listed_after["count"] == 1
    assert listed_after["routes"][0]["name"] == "b"

    _active_routes.clear()


@pytest.mark.asyncio
async def test_server_mock_network_route_includes_deprecation_notice():
    from blop import server

    with patch(
        "blop.server.network_tools.route_register",
        new=AsyncMock(return_value={"status": "registered"}),
    ) as handler:
        result = await server.mock_network_route(pattern="**/api/users", status=200)

    handler.assert_awaited_once()
    assert result["status"] == "registered"
    assert result["deprecation_notice"]["replacement_tool"] == "route_register"


@pytest.mark.asyncio
async def test_server_browser_route_includes_deprecation_notice():
    from blop import server

    with patch("blop.server.capability_flags.is_tool_enabled", return_value=True):
        with patch(
            "blop.server.browser_compat.browser_route",
            new=AsyncMock(return_value={"status": "registered"}),
        ) as handler:
            result = await server.browser_route(pattern="**/api/a", status=201, body='{"ok":true}')

    handler.assert_awaited_once()
    assert result["status"] == "registered"
    assert result["deprecation_notice"]["replacement_tool"] == "route_register"
    assert result["deprecation_notice"]["replacement_payload"]["scope"] == "compat_session"


@pytest.mark.asyncio
async def test_server_browser_storage_state_includes_deprecation_notice():
    from blop import server

    with patch("blop.server.capability_flags.is_tool_enabled", return_value=True):
        with patch(
            "blop.server.browser_compat.browser_storage_state",
            new=AsyncMock(return_value={"status": "ok", "path": "state.json"}),
        ) as handler:
            result = await server.browser_storage_state(filename="state.json")

    handler.assert_awaited_once_with(filename="state.json")
    assert result["status"] == "ok"
    assert result["deprecation_notice"]["replacement_tool"] == "storage_export"

