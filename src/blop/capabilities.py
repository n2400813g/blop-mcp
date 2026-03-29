"""Capability-based tool grouping for the Playwright-compat browser_* surface.

MCP registration is **not** fully driven by this module. Actual exposure is set in
``server.py`` using:

- **BLOP_ENABLE_LEGACY_MCP_TOOLS** (default ``false``): when ``true``, registers deprecated
  aliases ``discover_test_flows``, ``run_regression_test``, ``validate_setup``.
- **BLOP_ENABLE_COMPAT_TOOLS** (default ``false``): when ``true``, registers ``browser_*``,
  ``blop_v2_*``, assertions, reporting, storage helpers, etc.

The **always-on** surface (unless you change server code) includes: canonical release tools
(``validate_release_setup``, ``discover_critical_journeys``, ``run_release_check``,
``triage_release_blocker``), auth tools, context-read tools, atomic browser tools
(``navigate_to_url``, ``perform_step``, …), ``record_test_flow``, ``get_test_results``,
``cancel_run``, ``evaluate_web_task``, and related helpers.

``BLOP_CAPABILITIES`` / ``BLOP_CAPABILITIES_PROFILE`` select which **compat_browser** tool
names are allowed when compat mode is on: ``is_tool_enabled("browser_navigate")`` is used
by ``_safe_compat_call`` so a tool can be registered but blocked if the capability set does
not include ``compat_browser``.

Configure via ``BLOP_CAPABILITIES`` env (comma-separated) or ``--caps`` CLI arg.

Default legacy capability profile:
  development: "core,auth,debug"
  production: "core,auth"

Groups (for **compat_browser** gating and documentation):
  core       : always merged into ``get_enabled_tools()`` — regression helpers that compat
               callers may expect to be "on" when checking capability sets
  auth       : auth profile capture and persistence
  debug      : legacy validation/debug helpers (names only; many are compat-gated in server)
  analytics  : legacy analytics and run-status tool names
  v2         : v2 surface tool names
  assertions : structured assertion tools (verify_*)
  security   : security scanning tools
  reporting  : reporting and visual comparison helpers
  compat_browser : Playwright-MCP-compatible browser_* tool surface
  legacy_mcp : deprecated MCP aliases (discover_test_flows, run_regression_test,
               validate_setup) — **not** registered unless ``BLOP_ENABLE_LEGACY_MCP_TOOLS=true``
"""

from __future__ import annotations

import os

# Names gated by BLOP_ENABLE_LEGACY_MCP_TOOLS in server.py (documentation / tooling).
LEGACY_MCP_TOOL_NAMES: frozenset[str] = frozenset(
    {
        "discover_test_flows",
        "run_regression_test",
        "validate_setup",
    }
)

TOOL_GROUPS: dict[str, set[str]] = {
    "core": {
        "record_test_flow",
        "get_test_results",
    },
    "legacy_mcp": set(LEGACY_MCP_TOOL_NAMES),
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
        "get_qa_recommendations",
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
    """Return tool names considered enabled for **compat_browser** permission checks."""
    caps = get_enabled_capabilities()
    tools: set[str] = set()
    for cap in caps:
        tools.update(TOOL_GROUPS.get(cap, set()))
    tools.update(TOOL_GROUPS["core"])
    return tools


def is_tool_enabled(tool_name: str) -> bool:
    return tool_name in get_enabled_tools()
