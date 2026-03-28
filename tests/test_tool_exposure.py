"""Tests for MCP tool and resource registration — tool_exposure category."""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest


def _get_registered_tool_names() -> set[str]:
    """Return the set of tool names registered on server.mcp."""
    import blop.server as server

    tool_manager = server.mcp._tool_manager
    # FastMCP stores tools in _tools dict (name → Tool)
    return set(tool_manager._tools.keys())


CANONICAL_FOUR = {
    "validate_release_setup",
    "discover_critical_journeys",
    "run_release_check",
    "triage_release_blocker",
}


def test_canonical_four_tools_always_registered():
    """All 4 MVP canonical tools must be registered regardless of env flags."""
    import blop.server  # noqa: F401

    registered = _get_registered_tool_names()
    missing = CANONICAL_FOUR - registered
    assert not missing, f"Canonical tools not registered: {missing}"
    assert "get_mcp_capabilities" in registered


def test_authenticated_baseline_packager_is_registered():
    import blop.server  # noqa: F401

    registered = _get_registered_tool_names()
    assert "package_authenticated_saas_baseline" in registered


def test_context_and_atomic_tools_registered():
    import blop.server  # noqa: F401

    registered = _get_registered_tool_names()
    for name in (
        "get_workspace_context",
        "get_release_context",
        "get_journeys_for_release",
        "get_release_and_journeys",
        "get_prd_and_acceptance_criteria",
        "get_ux_taxonomy",
        "navigate_to_url",
        "navigate_to_journey",
        "get_page_snapshot",
        "perform_step",
        "capture_artifact",
        "record_run_observation",
    ):
        assert name in registered, f"missing {name}"


def test_legacy_alias_tools_absent_by_default():
    """Deprecated aliases must not register unless BLOP_ENABLE_LEGACY_MCP_TOOLS=true."""
    import blop.config as cfg

    if not cfg.BLOP_ENABLE_LEGACY_MCP_TOOLS:
        registered = _get_registered_tool_names()
        for name in ("discover_test_flows", "run_regression_test", "validate_setup"):
            assert name not in registered, f"{name} must not register when legacy MCP tools are off"


def test_compat_tools_absent_by_default():
    """With BLOP_ENABLE_COMPAT_TOOLS unset, compat tools must not appear."""
    # We can't re-register — just verify the current registration state
    # (server was imported without the flag set by default in tests)

    # If compat tools are off, browser_navigate and list_runs should not be registered
    from blop.config import BLOP_ENABLE_COMPAT_TOOLS

    if not BLOP_ENABLE_COMPAT_TOOLS:
        registered = _get_registered_tool_names()
        assert "browser_navigate" not in registered, "browser_navigate must not be registered in default mode"
        assert "list_runs" not in registered, "list_runs must not be registered in default mode"


def test_all_mvp_resources_registered():
    """blop://journeys and blop://release/* resources must be registered."""
    import blop.server as server

    resource_manager = server.mcp._resource_manager
    # Exact (non-template) URIs
    exact_uris = set(resource_manager._resources.keys())
    # Template URIs (e.g. blop://release/{release_id}/brief)
    template_uris = set(getattr(resource_manager, "_templates", {}).keys())
    all_uri_str = " ".join(str(u) for u in exact_uris | template_uris)

    assert "blop://journeys" in all_uri_str, (
        f"blop://journeys not found. Exact: {exact_uris}, Templates: {template_uris}"
    )
    assert "blop://release" in all_uri_str, (
        f"blop://release/* not found. Exact: {exact_uris}, Templates: {template_uris}"
    )
    assert "blop://health" in all_uri_str, f"blop://health not found. Exact: {exact_uris}, Templates: {template_uris}"


def test_safe_call_wraps_exceptions():
    """_safe_call must return an error dict when the handler raises."""
    import asyncio

    import blop.server as server

    async def _failing_handler(**kwargs):
        raise ValueError("boom")

    result = asyncio.run(server._safe_call(_failing_handler, tool_name="test_tool"))
    assert "error" in result
    assert result.get("error_type") == "ValueError"
    assert result.get("tool") == "test_tool"


def test_legacy_docstrings_point_to_canonical_alternatives():
    import blop.server as server

    assert "discover_critical_journeys" in (server.discover_test_flows.__doc__ or "")
    assert "run_release_check" in (server.run_regression_test.__doc__ or "")
    assert "blop://journeys resource" in (server.list_recorded_tests.__doc__ or "")


def test_prompt_resources_marked_internal_debug():
    import blop.server as server

    assert "Debug/internal resource" in (server.prompts_list_resource.__doc__ or "")
    assert "Debug/internal resource" in (server.prompt_resource.__doc__ or "")


@pytest.mark.asyncio
async def test_get_mcp_capabilities_returns_ok_envelope():
    import blop.server as server

    out = await server.get_mcp_capabilities()
    assert out.get("ok") is True
    data = out.get("data") or {}
    assert data.get("health_resource_uri") == "blop://health"
    assert isinstance(data.get("registered_tool_count"), int)
    assert "canonical_release_tools" in data


@pytest.mark.asyncio
async def test_health_resource_includes_runtime_posture_and_path_checks(monkeypatch):
    import blop.server as server

    monkeypatch.setattr(server.sqlite, "list_runs", AsyncMock(return_value=[]))
    monkeypatch.setattr(server.regression, "_RUN_TASKS", {})

    async def fake_posture():
        return {}

    with monkeypatch.context() as m:
        m.setattr("shutil.which", lambda _name: "/usr/bin/chromium")
        m.setattr(
            "blop.config.runtime_posture_snapshot",
            lambda: {
                "environment": "production",
                "paths": {"db_path_absolute": True, "debug_log": "/tmp/blop.log"},
            },
        )
        out = await server.health_resource()

    assert out["db_reachable"] is True
    assert out["chromium_found"] is True
    assert out["runtime_posture"]["environment"] == "production"
    assert out["path_checks"]["db_path_absolute"] is True
