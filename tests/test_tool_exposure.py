"""Tests for MCP tool and resource registration — tool_exposure category."""

from __future__ import annotations

import importlib
import sys
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
    assert "get_process_insights" in registered
    assert "export_run_trace" in registered


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
    assert result.get("blop_error", {}).get("code") == "BLOP_MCP_INTERNAL_TOOL_ERROR"


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


def _clear_tools_imports(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in (
        "blop.api.v1.router",
        "blop.engine.browser_pool",
        "blop.engine.discovery",
        "blop.tools",
        "blop.tools.compat",
        "blop.tools.context_read",
        "blop.tools.evaluate",
        "blop.tools.journeys",
        "blop.tools.resources",
        "blop.tools.release_check",
        "blop.tools.results",
        "blop.tools.triage",
        "blop.tools.validate",
    ):
        monkeypatch.delitem(sys.modules, name, raising=False)
        parent_name, _, child_name = name.rpartition(".")
        if parent_name:
            parent_module = sys.modules.get(parent_name)
            if parent_module is not None:
                monkeypatch.delattr(parent_module, child_name, raising=False)


def _clear_server_imports(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in (
        "blop.server",
        "blop.storage.sqlite",
        "blop.tools.assertions",
        "blop.tools.atomic_browser",
        "blop.tools.auth",
        "blop.tools.baselines",
        "blop.tools.browser_compat",
        "blop.tools.capture_auth",
        "blop.tools.context_read",
        "blop.tools.debug",
        "blop.tools.discover",
        "blop.tools.evaluate",
        "blop.tools.journeys",
        "blop.tools.network",
        "blop.tools.prompts",
        "blop.tools.record",
        "blop.tools.regression",
        "blop.tools.release_check",
        "blop.tools.resources",
        "blop.tools.results",
        "blop.tools.security",
        "blop.tools.storage",
        "blop.tools.triage",
        "blop.tools.v2_surface",
        "blop.tools.validate",
    ):
        monkeypatch.delitem(sys.modules, name, raising=False)
        parent_name, _, child_name = name.rpartition(".")
        if parent_name:
            parent_module = sys.modules.get(parent_name)
            if parent_module is not None:
                monkeypatch.delattr(parent_module, child_name, raising=False)


def test_tools_package_import_is_lazy(monkeypatch):
    _clear_tools_imports(monkeypatch)

    importlib.import_module("blop.tools")

    assert "blop.tools.compat" not in sys.modules
    assert "blop.tools.journeys" not in sys.modules
    assert "blop.tools.release_check" not in sys.modules
    assert "blop.tools.triage" not in sys.modules
    assert "blop.tools.validate" not in sys.modules


def test_tools_lazy_export_loads_only_requested_module(monkeypatch):
    _clear_tools_imports(monkeypatch)

    tools_pkg = importlib.import_module("blop.tools")
    validate_release_setup = tools_pkg.validate_release_setup

    assert callable(validate_release_setup)
    assert "blop.tools.validate" in sys.modules
    assert "blop.tools.compat" not in sys.modules
    assert "blop.tools.journeys" not in sys.modules
    assert "blop.tools.release_check" not in sys.modules


def test_compat_module_import_is_lazy(monkeypatch):
    _clear_tools_imports(monkeypatch)

    importlib.import_module("blop.tools.compat")

    assert "blop.tools.compat" in sys.modules
    assert "blop.tools.evaluate" not in sys.modules
    assert "blop.engine.browser_pool" not in sys.modules
    assert "blop.engine.discovery" not in sys.modules
    assert "blop.tools.journeys" not in sys.modules
    assert "blop.tools.resources" not in sys.modules
    assert "blop.tools.release_check" not in sys.modules
    assert "blop.tools.validate" not in sys.modules


def test_context_read_import_is_lazy(monkeypatch):
    _clear_tools_imports(monkeypatch)

    importlib.import_module("blop.tools.context_read")

    assert "blop.tools.context_read" in sys.modules
    assert "blop.tools.resources" not in sys.modules


@pytest.mark.asyncio
async def test_compat_validate_loads_only_validate_module(monkeypatch):
    _clear_tools_imports(monkeypatch)

    compat = importlib.import_module("blop.tools.compat")
    monkeypatch.setattr(compat, "BLOP_ENABLE_COMPAT_TOOLS", True)

    with monkeypatch.context() as m:
        m.setattr(
            "blop.tools.validate.validate_release_setup",
            AsyncMock(return_value={"status": "ready"}),
        )
        result = await compat.validate_setup(app_url="https://example.com")

    assert result["deprecated"] is True
    assert "blop.tools.validate" in sys.modules
    assert "blop.tools.evaluate" not in sys.modules
    assert "blop.engine.browser_pool" not in sys.modules
    assert "blop.engine.discovery" not in sys.modules
    assert "blop.tools.journeys" not in sys.modules
    assert "blop.tools.resources" not in sys.modules
    assert "blop.tools.release_check" not in sys.modules


def test_v1_router_import_is_lazy(monkeypatch):
    pytest.importorskip("fastapi")
    _clear_tools_imports(monkeypatch)

    importlib.import_module("blop.api.v1.router")

    assert "blop.api.v1.router" in sys.modules
    assert "blop.tools.release_check" not in sys.modules
    assert "blop.tools.results" not in sys.modules


@pytest.mark.asyncio
async def test_v1_router_release_check_wrapper_loads_only_release_check(monkeypatch):
    pytest.importorskip("fastapi")
    _clear_tools_imports(monkeypatch)

    router = importlib.import_module("blop.api.v1.router")

    with monkeypatch.context() as m:
        m.setattr(
            "blop.tools.release_check.run_release_check",
            AsyncMock(return_value={"run_id": "run-1", "status": "queued"}),
        )
        result = await router.run_release_check(app_url="https://example.com")

    assert result["run_id"] == "run-1"
    assert "blop.tools.release_check" in sys.modules
    assert "blop.tools.results" not in sys.modules


def test_server_import_is_lazy(monkeypatch):
    _clear_server_imports(monkeypatch)

    importlib.import_module("blop.server")

    assert "blop.server" in sys.modules
    assert "blop.storage.sqlite" not in sys.modules
    assert "blop.tools.atomic_browser" not in sys.modules
    assert "blop.tools.context_read" not in sys.modules
    assert "blop.tools.journeys" not in sys.modules
    assert "blop.tools.prompts" not in sys.modules
    assert "blop.tools.regression" not in sys.modules
    assert "blop.tools.release_check" not in sys.modules
    assert "blop.tools.results" not in sys.modules
    assert "blop.tools.validate" not in sys.modules


def test_server_validate_proxy_loads_only_validate(monkeypatch):
    _clear_server_imports(monkeypatch)

    server = importlib.import_module("blop.server")
    handler = server.validate.validate_release_setup

    assert callable(handler)
    assert "blop.tools.validate" in sys.modules
    assert "blop.storage.sqlite" not in sys.modules
    assert "blop.tools.journeys" not in sys.modules
    assert "blop.tools.regression" not in sys.modules
    assert "blop.tools.results" not in sys.modules
