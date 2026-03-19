"""Capability-based tool grouping — selectively enable MCP tools.

Configure via BLOP_CAPABILITIES env var (comma-separated) or --caps CLI arg.
Default: "core,auth,debug"

Groups:
  core       : discover, explore_site_inventory, record, run, results, list_recorded_tests (always on)
  auth       : save_auth_profile, capture_auth_session
  debug      : debug_test_case, validate_setup, get_page_structure
  analytics  : get_risk_analytics, get_run_health_stream, list_runs
  v2         : all v2 surface tools
  assertions : structured assertion tools (verify_*)
  security   : security scanning tools
  reporting  : export_test_report, compare_visual_baseline
"""
from __future__ import annotations

import os

TOOL_GROUPS: dict[str, set[str]] = {
    "core": {
        "discover_test_flows",
        "explore_site_inventory",
        "record_test_flow",
        "run_regression_test",
        "get_test_results",
        "list_recorded_tests",
    },
    "auth": {
        "save_auth_profile",
        "capture_auth_session",
    },
    "debug": {
        "debug_test_case",
        "validate_setup",
        "get_page_structure",
    },
    "analytics": {
        "get_risk_analytics",
        "get_run_health_stream",
        "list_runs",
    },
    "v2": {
        "blop_v2_get_surface_contract",
        "blop_v2_capture_context",
        "blop_v2_compare_context",
        "blop_v2_assess_release_risk",
        "blop_v2_get_journey_health",
        "blop_v2_cluster_incidents",
        "blop_v2_generate_remediation",
        "blop_v2_ingest_telemetry_signals",
        "blop_v2_get_correlation_report",
        "blop_v2_suggest_flows_for_diff",
        "blop_v2_autogenerate_flows",
        "blop_v2_archive_storage",
    },
    "assertions": {
        "verify_element_visible",
        "verify_text_visible",
        "verify_value",
        "verify_visual_state",
    },
    "security": {
        "security_scan",
        "security_scan_url",
    },
    "reporting": {
        "export_test_report",
        "compare_visual_baseline",
    },
}

DEFAULT_CAPABILITIES = "core,auth,debug"


def get_enabled_capabilities() -> list[str]:
    raw = os.getenv("BLOP_CAPABILITIES", DEFAULT_CAPABILITIES)
    return [c.strip().lower() for c in raw.split(",") if c.strip()]


def get_enabled_tools() -> set[str]:
    """Return the set of tool names that should be exposed based on enabled capabilities."""
    caps = get_enabled_capabilities()
    tools: set[str] = set()
    for cap in caps:
        tools.update(TOOL_GROUPS.get(cap, set()))
    # core is always on
    tools.update(TOOL_GROUPS["core"])
    return tools


def is_tool_enabled(tool_name: str) -> bool:
    return tool_name in get_enabled_tools()
