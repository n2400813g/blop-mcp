"""Capability-based tool grouping — selectively enable MCP tools.

Configure via BLOP_CAPABILITIES env var (comma-separated) or --caps CLI arg.
Default capability groups still control the optional legacy surface.
The canonical MVP tools are registered separately and are always available:
  validate_release_setup
  discover_critical_journeys
  run_release_check
  triage_release_blocker

Default legacy capability profile:
  development: "core,auth,debug"
  production: "core,auth"

Groups:
  core       : legacy discover/record/run/results tools
  auth       : auth profile capture and persistence
  debug      : legacy validation/debug helpers
  analytics  : legacy analytics and run-status tools
  v2         : v2 surface tools
  assertions : structured assertion tools (verify_*)
  security   : security scanning tools
  reporting  : reporting and visual comparison helpers
  compat_browser : Playwright-MCP-compatible browser_* tool surface
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
    "compat_browser": {
        "browser_navigate",
        "browser_navigate_back",
        "browser_snapshot",
        "browser_click",
        "browser_type",
        "browser_hover",
        "browser_select_option",
        "browser_file_upload",
        "browser_tabs",
        "browser_close",
        "browser_console_messages",
        "browser_network_requests",
        "browser_take_screenshot",
        "browser_wait_for",
        "browser_press_key",
        "browser_resize",
        "browser_handle_dialog",
        "browser_route",
        "browser_unroute",
        "browser_route_list",
        "browser_network_state_set",
        "browser_cookie_list",
        "browser_cookie_get",
        "browser_cookie_set",
        "browser_cookie_delete",
        "browser_cookie_clear",
        "browser_storage_state",
        "browser_set_storage_state",
        "browser_localstorage_list",
        "browser_localstorage_get",
        "browser_localstorage_set",
        "browser_localstorage_delete",
        "browser_localstorage_clear",
        "browser_sessionstorage_list",
        "browser_sessionstorage_get",
        "browser_sessionstorage_set",
        "browser_sessionstorage_delete",
        "browser_sessionstorage_clear",
    },
}

_DEFAULT_CAPS_BY_ENV = {
    "production": "core,auth",
    "default": "core,auth,debug",
}
DEFAULT_CAPABILITIES = _DEFAULT_CAPS_BY_ENV.get(
    os.getenv("BLOP_ENV", "development").strip().lower(),
    _DEFAULT_CAPS_BY_ENV["default"],
)
CAPABILITY_PROFILES: dict[str, str] = {
    "production_minimal": "core,auth",
    "production_debug": "core,auth,debug,analytics",
    "full": "core,auth,debug,analytics,v2,assertions,security,reporting,compat_browser",
}


def get_enabled_capabilities() -> list[str]:
    profile = os.getenv("BLOP_CAPABILITIES_PROFILE", "").strip().lower()
    if profile:
        raw = CAPABILITY_PROFILES.get(profile, "")
        if not raw:
            raw = os.getenv("BLOP_CAPABILITIES", DEFAULT_CAPABILITIES)
    else:
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
