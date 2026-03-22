"""Tests for MCP tool and resource registration — tool_exposure category."""
from __future__ import annotations

import os
import sys
from unittest.mock import AsyncMock


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


def test_authenticated_baseline_packager_is_registered():
    import blop.server  # noqa: F401

    registered = _get_registered_tool_names()
    assert "package_authenticated_saas_baseline" in registered


def test_compat_tools_absent_by_default():
    """With BLOP_ENABLE_COMPAT_TOOLS unset, compat tools must not appear."""
    env = {k: v for k, v in os.environ.items() if k != "BLOP_ENABLE_COMPAT_TOOLS"}
    # We can't re-register — just verify the current registration state
    # (server was imported without the flag set by default in tests)
    import blop.server as server

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

    assert "blop://journeys" in all_uri_str, f"blop://journeys not found. Exact: {exact_uris}, Templates: {template_uris}"
    assert "blop://release" in all_uri_str, f"blop://release/* not found. Exact: {exact_uris}, Templates: {template_uris}"
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
