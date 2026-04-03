"""Blop FastMCP server — release confidence control plane.

**Core MCP tools (default agent path)** — ``validate_release_setup``,
``discover_critical_journeys``, ``record_test_flow``, ``run_release_check``,
``triage_release_blocker``, context-read tools (``get_workspace_context``,
``get_release_and_journeys``, …), ``get_mcp_capabilities``, and atomic browser
tools (``navigate_to_url``, ``perform_step``, …).

**Full release context in three or fewer tool calls**
1. ``validate_release_setup(app_url=…)`` when preflight is needed.
2. ``get_release_and_journeys(release_id)`` — or ``get_workspace_context()`` plus
   the ``blop://journeys`` resource.
3. ``run_release_check(…)``, then ``get_test_results(run_id)`` or
   ``blop://release/{release_id}/brief`` for the decision.

**Typical pipeline** — ``discover_critical_journeys`` → ``record_test_flow`` per
journey → ``run_release_check(mode="replay")`` → read
``blop://release/{release_id}/brief`` or ``get_release_context``.

**Surface gating** — ``BLOP_ENABLE_LEGACY_MCP_TOOLS`` registers deprecated aliases
(``discover_test_flows``, ``run_regression_test``, ``validate_setup``).
``BLOP_ENABLE_COMPAT_TOOLS`` registers ``browser_*``, ``blop_v2_*``, and related
extras. Logging is disabled before other imports so stdio JSON-RPC stays clean.
"""

import logging
import os
import sqlite3
import uuid
from importlib import import_module
from pathlib import Path
from urllib.parse import unquote

# Must happen before any other imports to prevent JSON-RPC interference
logging.disable(logging.CRITICAL)
os.environ.setdefault("ANONYMIZED_TELEMETRY", "false")
os.environ.setdefault("BROWSER_USE_LOGGING_LEVEL", "CRITICAL")

# blop logger bypasses logging.disable via explicit _logger.disabled = False
from blop.engine.logger import get_logger as _get_blop_logger  # noqa: E402
from blop.engine.logger import request_id_var  # noqa: E402

_log = _get_blop_logger("server")

from typing import Literal, Optional

from mcp.server.fastmcp import FastMCP

from blop import capabilities as capability_flags
from blop.config import BLOP_DISCOVERY_MAX_PAGES
from blop.engine.errors import (
    BLOP_CAPABILITY_DISABLED,
    BLOP_MCP_INTERNAL_TOOL_ERROR,
    BLOP_RUN_NOT_FOUND,
    BlopError,
    blop_error_from_sqlite,
    tool_error,
)
from blop.schemas import ReleaseReference, TelemetrySignalInput


class _LazyModule:
    """Proxy module imports until first attribute access.

    This keeps ``import blop.server`` lighter while preserving existing patch
    points such as ``blop.server.browser_compat.browser_hover`` in tests.
    """

    __slots__ = ("_module_name", "_module")

    def __init__(self, module_name: str) -> None:
        object.__setattr__(self, "_module_name", module_name)
        object.__setattr__(self, "_module", None)

    def _load(self):
        module = object.__getattribute__(self, "_module")
        if module is None:
            module = import_module(object.__getattribute__(self, "_module_name"))
            object.__setattr__(self, "_module", module)
        return module

    def __getattr__(self, name: str):
        return getattr(self._load(), name)

    def __setattr__(self, name: str, value) -> None:
        if name in self.__slots__:
            object.__setattr__(self, name, value)
            return
        setattr(self._load(), name, value)

    def __delattr__(self, name: str) -> None:
        if name in self.__slots__:
            object.__delattr__(self, name)
            return
        delattr(self._load(), name)

    def __repr__(self) -> str:
        return f"<LazyModule {object.__getattribute__(self, '_module_name')}>"


def _lazy_module(module_name: str) -> _LazyModule:
    return _LazyModule(module_name)


sqlite = _lazy_module("blop.storage.sqlite")
assertions_tools = _lazy_module("blop.tools.assertions")
auth = _lazy_module("blop.tools.auth")
browser_compat = _lazy_module("blop.tools.browser_compat")
capture_auth = _lazy_module("blop.tools.capture_auth")
debug = _lazy_module("blop.tools.debug")
discover = _lazy_module("blop.tools.discover")
record = _lazy_module("blop.tools.record")
regression = _lazy_module("blop.tools.regression")
results = _lazy_module("blop.tools.results")
v2_surface = _lazy_module("blop.tools.v2_surface")
validate = _lazy_module("blop.tools.validate")
baselines_tools = _lazy_module("blop.tools.baselines")
evaluate_tools = _lazy_module("blop.tools.evaluate")
network_tools = _lazy_module("blop.tools.network")
security_tools = _lazy_module("blop.tools.security")
storage_tools = _lazy_module("blop.tools.storage")
atomic_browser_tools = _lazy_module("blop.tools.atomic_browser")
context_read_tools = _lazy_module("blop.tools.context_read")
journeys_tools = _lazy_module("blop.tools.journeys")
release_check_tools = _lazy_module("blop.tools.release_check")
resources_tools = _lazy_module("blop.tools.resources")
triage_tools = _lazy_module("blop.tools.triage")
prompts_tools = _lazy_module("blop.tools.prompts")
process_insights_tools = _lazy_module("blop.tools.process_insights")
qa_advisor_tools = _lazy_module("blop.tools.qa_advisor")

mcp = FastMCP("blop")


def _ensure_compat_enabled(tool_name: str) -> Optional[dict]:
    if capability_flags.is_tool_enabled(tool_name):
        return None
    return tool_error(
        (
            f"Tool '{tool_name}' is disabled by capabilities. "
            "Enable BLOP_CAPABILITIES=...,compat_browser to use Playwright-compatible browser_* tools."
        ),
        BLOP_CAPABILITY_DISABLED,
        details={"tool": tool_name},
    )


async def _safe_call(handler, /, tool_name: Optional[str] = None, **kwargs) -> dict:
    """Standardized error envelope for MCP tool handlers."""
    rid = uuid.uuid4().hex[:12]
    token = request_id_var.set(rid)
    try:
        try:
            result = await handler(**kwargs)
        except BlopError as e:
            result = e.to_merged_response()
        except sqlite3.Error as e:
            # aiosqlite.Error subclasses sqlite3.Error
            result = blop_error_from_sqlite(e).to_merged_response()
        except Exception as e:
            _tool = tool_name or getattr(handler, "__qualname__", str(handler))
            _log.error(
                "tool_exception tool=%s error_type=%s",
                _tool,
                type(e).__name__,
                extra={
                    "event": "tool_exception",
                    "tool": _tool,
                    "error_type": type(e).__name__,
                    "error": str(e),
                    "run_id": kwargs.get("run_id"),
                    "flow_id": kwargs.get("flow_id"),
                    "case_id": kwargs.get("case_id"),
                    "profile_name": kwargs.get("profile_name"),
                },
                exc_info=True,
            )
            payload = tool_error(
                (f"Tool '{_tool}' encountered an internal error. Check the configured BLOP_DEBUG_LOG for details."),
                BLOP_MCP_INTERNAL_TOOL_ERROR,
                details={"error_type": type(e).__name__, "tool": _tool},
                error_type=type(e).__name__,
                tool=_tool,
                request_id=rid,
            )
            for key in ("run_id", "flow_id", "case_id"):
                value = kwargs.get(key)
                if isinstance(value, str) and value:
                    payload[key] = value
            return payload
        if isinstance(result, dict):
            result = dict(result)
            result.setdefault("request_id", rid)
        return result
    finally:
        request_id_var.reset(token)


async def _safe_compat_call(tool_name: str, handler, /, **kwargs) -> dict:
    blocked = _ensure_compat_enabled(tool_name)
    if blocked:
        return blocked
    return await _safe_call(handler, tool_name=tool_name, **kwargs)


def _add_deprecation_notice(
    payload: dict,
    *,
    replacement_tool: str,
    replacement_payload: dict,
    message: str = "This tool is deprecated.",
    removal_phase: str = "after_one_stable_release",
) -> dict:
    enriched = dict(payload)
    enriched["deprecation_notice"] = {
        "message": message,
        "replacement_tool": replacement_tool,
        "replacement_payload": replacement_payload,
        "removal_phase": removal_phase,
    }
    return enriched


def _compat_tools_enabled() -> bool:
    """Return True when BLOP_ENABLE_COMPAT_TOOLS=true.

    Gates Playwright-compat browser_* names, blop_v2_*, and other legacy tools.
    Default False: core release, context-read, and atomic browser tools still register.
    Set True to restore the expanded legacy surface (dozens of tools).
    """
    from blop.config import BLOP_ENABLE_COMPAT_TOOLS

    return BLOP_ENABLE_COMPAT_TOOLS


def _if_compat(fn):
    """Decorator: register fn as an MCP tool only when BLOP_ENABLE_COMPAT_TOOLS=true."""
    if _compat_tools_enabled():
        return mcp.tool()(fn)
    return fn


def _legacy_mcp_tools_enabled() -> bool:
    """Return True when BLOP_ENABLE_LEGACY_MCP_TOOLS=true.

    Gates deprecated MCP aliases: discover_test_flows, run_regression_test, validate_setup.
    Default False: use discover_critical_journeys, run_release_check, validate_release_setup.
    """
    from blop.config import BLOP_ENABLE_LEGACY_MCP_TOOLS

    return BLOP_ENABLE_LEGACY_MCP_TOOLS


def _if_legacy(fn):
    """Decorator: register fn as an MCP tool only when BLOP_ENABLE_LEGACY_MCP_TOOLS=true."""
    if _legacy_mcp_tools_enabled():
        return mcp.tool()(fn)
    return fn


@_if_legacy
async def discover_test_flows(
    app_url: str,
    repo_path: Optional[str] = None,
    profile_name: Optional[str] = None,
    business_goal: Optional[str] = None,
    command: Optional[str] = None,
    max_depth: int = 2,
    max_pages: int = BLOP_DISCOVERY_MAX_PAGES,
    seed_urls: Optional[list[str]] = None,
    include_url_pattern: Optional[str] = None,
    exclude_url_pattern: Optional[str] = None,
    return_inventory: bool = False,
) -> dict:
    """[DEPRECATED — use discover_critical_journeys for release planning] Discover test flows for an application by scanning its pages or source code.

    Uses a BFS crawl to extract page signals (CTAs, auth routes, forms, headings),
    then sends them to Gemini to generate 5-8 meaningful test flows with severity hints.
    This legacy tool returns raw planned flows; the canonical replacement returns
    business-ranked journeys with clearer release-gating context.

    Args:
        app_url: The website URL to scan
        repo_path: Optional path to local source directory for code-based flow generation
        profile_name: Optional auth profile name to use during crawl (for auth-gated pages)
        business_goal: Optional plain-English business goal to prioritize in flow planning
        command: Optional natural language command (parsed for intent/scope/priorities)
        max_depth: BFS crawl depth (default 2)
        max_pages: Maximum pages to crawl (default 10)
        seed_urls: Optional list of same-origin URLs to prioritize
        include_url_pattern: Optional regex; only crawl matching URLs
        exclude_url_pattern: Optional regex; skip matching URLs
        return_inventory: If true, include raw inventory in response

    Returns:
        dict with app_url, inventory_summary, flows, flow_count, quality (+inventory when requested)
    """
    return await _safe_call(
        discover.discover_test_flows,
        app_url=app_url,
        repo_path=repo_path,
        profile_name=profile_name,
        business_goal=business_goal,
        command=command,
        max_depth=max_depth,
        max_pages=max_pages,
        seed_urls=seed_urls,
        include_url_pattern=include_url_pattern,
        exclude_url_pattern=exclude_url_pattern,
        return_inventory=return_inventory,
    )


@_if_compat
async def explore_site_inventory(
    app_url: str,
    profile_name: Optional[str] = None,
    max_depth: int = 2,
    max_pages: int = BLOP_DISCOVERY_MAX_PAGES,
    seed_urls: Optional[list[str]] = None,
    include_url_pattern: Optional[str] = None,
    exclude_url_pattern: Optional[str] = None,
) -> dict:
    """Explore site structure without generating flows.

    Performs crawl-only discovery and returns routes, links, buttons, forms, headings,
    auth signals, business signals, and compact per-page interactive ARIA structure.
    Useful when you want to inspect site topology first, then call discover_test_flows
    with tighter scope.
    """
    return await _safe_call(
        discover.explore_site_inventory,
        app_url=app_url,
        profile_name=profile_name,
        max_depth=max_depth,
        max_pages=max_pages,
        seed_urls=seed_urls,
        include_url_pattern=include_url_pattern,
        exclude_url_pattern=exclude_url_pattern,
    )


@_if_compat
async def get_page_structure(
    app_url: str,
    url: Optional[str] = None,
    profile_name: Optional[str] = None,
) -> dict:
    """Capture interactive page structure (ARIA roles/names) for one URL.

    Useful when you want compact layout context before recording or repairing a flow.
    This returns a flattened list of interactive nodes from Playwright's accessibility tree.

    Args:
        app_url: Base app URL for context
        url: Optional target URL to inspect (defaults to app_url)
        profile_name: Optional auth profile for protected pages
    """
    return await _safe_call(
        discover.get_page_structure,
        app_url=app_url,
        url=url,
        profile_name=profile_name,
    )


@mcp.tool()
async def save_auth_profile(
    profile_name: str,
    auth_type: Literal["env_login", "storage_state", "cookie_json"],
    login_url: Optional[str] = None,
    username_env: Optional[str] = "TEST_USERNAME",
    password_env: Optional[str] = "TEST_PASSWORD",
    storage_state_path: Optional[str] = None,
    cookie_json_path: Optional[str] = None,
    user_data_dir: Optional[str] = None,
) -> dict:
    """Save an authentication profile for use in test runs.

    Args:
        profile_name: Unique name for this profile
        auth_type: One of "env_login", "storage_state", or "cookie_json"
        login_url: Login page URL (required for env_login)
        username_env: Name of env var holding the username (default: TEST_USERNAME)
        password_env: Name of env var holding the password (default: TEST_PASSWORD)
        storage_state_path: Path to a Playwright storage_state.json file
        cookie_json_path: Path to a JSON file containing cookie objects
        user_data_dir: Optional path to a persistent Chromium profile directory (helps with anti-bot OAuth)

    Returns:
        dict with profile_name, auth_type, status, note
    """
    return await _safe_call(
        auth.save_auth_profile,
        profile_name=profile_name,
        auth_type=auth_type,
        login_url=login_url,
        username_env=username_env,
        password_env=password_env,
        storage_state_path=storage_state_path,
        cookie_json_path=cookie_json_path,
        user_data_dir=user_data_dir,
    )


@mcp.tool()
async def capture_auth_session(
    profile_name: str,
    login_url: str,
    success_url_pattern: Optional[str] = None,
    timeout_secs: int = 120,
    user_data_dir: Optional[str] = None,
) -> dict:
    """Open a headed browser for interactive OAuth/MFA login and save the session state.

    A browser window opens — complete Google/GitHub OAuth or any MFA flow manually.
    The tool polls the URL every 500ms and saves storage state automatically once login succeeds.

    Args:
        profile_name: Name to save the auth profile under
        login_url: URL of the login page to open
        success_url_pattern: URL substring that indicates successful login (e.g. "/dashboard")
                             If omitted, any URL change away from login_url counts as success
        timeout_secs: Max seconds to wait for login (default: 120)
        user_data_dir: Optional path to a persistent Chromium profile dir (for OAuth providers
                       that detect fresh browser contexts as bots, e.g. Google, LinkedIn)

    Returns:
        dict with profile_name, requested_profile_name, status ("captured" | "timeout" | "error"), storage_state_path, note
    """
    return await _safe_call(
        capture_auth.capture_auth_session,
        profile_name=profile_name,
        login_url=login_url,
        success_url_pattern=success_url_pattern,
        timeout_secs=timeout_secs,
        user_data_dir=user_data_dir,
    )


@_if_compat
async def list_auth_profiles() -> dict:
    """List all saved auth profiles.

    Returns:
        dict with profiles list (profile_name, auth_type, storage_state_path, created_at, refreshed_at)
    """
    try:
        from blop.storage import sqlite

        profiles = await sqlite.list_auth_profiles()
        return {"profiles": profiles, "total": len(profiles)}
    except Exception as e:
        return tool_error(str(e), BLOP_MCP_INTERNAL_TOOL_ERROR)


@mcp.tool()
async def evaluate_web_task(
    task: str,
    app_url: Optional[str] = None,
    profile_name: Optional[str] = None,
    headless: bool = False,
    max_steps: int = 25,
    capture: Optional[list[str]] = None,
    format: str = "markdown",
    save_as_recorded_flow: bool = False,
    flow_name: Optional[str] = None,
) -> dict:
    """Run a browser agent for a natural-language task and return a rich evaluation report.

    One-shot evaluator — give it a URL and a task, get back a structured report with
    screenshots, console errors, network failures, and an agent step timeline. No need
    to discover/record/replay first.

    Args:
        task: Natural-language description of what to test (e.g. "Try the signup flow and note UX issues")
        app_url: The website URL to evaluate. Omitted when APP_BASE_URL is set in the server environment.
        profile_name: Optional auth profile name for authenticated pages
        headless: Run browser in headless mode (default: False — shows the browser)
        max_steps: Maximum agent steps (default: 25)
        capture: Evidence to capture: "screenshots", "console", "network", "trace" (default: all four)
        format: Report format: "markdown" (default), "text", or "json"
        save_as_recorded_flow: If True, promote the evaluation into a recorded flow for regression
        flow_name: Flow name to use when saving as recorded flow (auto-generated if omitted)

    Returns:
        dict with summary, agent_steps, evidence (console_errors, network_failures,
        screenshots, trace_path), pass_fail, run_id, and formatted_report
    """
    return await _safe_call(
        evaluate_tools.evaluate_web_task,
        task=task,
        app_url=app_url,
        profile_name=profile_name,
        headless=headless,
        max_steps=max_steps,
        capture=capture,
        format=format,
        save_as_recorded_flow=save_as_recorded_flow,
        flow_name=flow_name,
    )


# ---------------------------------------------------------------------------
# Playwright-MCP compatibility tools (capability-gated via compat_browser)
# ---------------------------------------------------------------------------


@_if_compat
async def browser_navigate(url: str, profile_name: Optional[str] = None) -> dict:
    """Navigate the shared compat browser session to a URL."""
    return await _safe_compat_call(
        "browser_navigate",
        browser_compat.browser_navigate,
        url=url,
        profile_name=profile_name,
    )


@_if_compat
async def browser_navigate_back() -> dict:
    """Navigate the shared compat browser session back one page."""
    return await _safe_compat_call("browser_navigate_back", browser_compat.browser_navigate_back)


@_if_compat
async def browser_snapshot(filename: Optional[str] = None, selector: Optional[str] = None) -> dict:
    """Capture a page snapshot from the shared compat browser session."""
    return await _safe_compat_call(
        "browser_snapshot",
        browser_compat.browser_snapshot,
        filename=filename,
        selector=selector,
    )


@_if_compat
async def browser_click(
    ref: Optional[str] = None,
    selector: Optional[str] = None,
    double_click: bool = False,
) -> dict:
    """Click an element in the shared compat browser session."""
    return await _safe_compat_call(
        "browser_click",
        browser_compat.browser_click,
        ref=ref,
        selector=selector,
        double_click=double_click,
    )


@_if_compat
async def browser_type(
    text: str,
    ref: Optional[str] = None,
    selector: Optional[str] = None,
    submit: bool = False,
    slowly: bool = False,
) -> dict:
    """Type text into an element in the shared compat browser session."""
    return await _safe_compat_call(
        "browser_type",
        browser_compat.browser_type,
        text=text,
        ref=ref,
        selector=selector,
        submit=submit,
        slowly=slowly,
    )


@_if_compat
async def browser_hover(ref: Optional[str] = None, selector: Optional[str] = None) -> dict:
    """Hover an element in the shared compat browser session."""
    return await _safe_compat_call(
        "browser_hover",
        browser_compat.browser_hover,
        ref=ref,
        selector=selector,
    )


@_if_compat
async def browser_select_option(
    values: list[str],
    ref: Optional[str] = None,
    selector: Optional[str] = None,
) -> dict:
    """Select one or more options in the shared compat browser session."""
    return await _safe_compat_call(
        "browser_select_option",
        browser_compat.browser_select_option,
        values=values,
        ref=ref,
        selector=selector,
    )


@_if_compat
async def browser_file_upload(paths: Optional[list[str]] = None) -> dict:
    """Upload files through the currently focused file input in shared compat session."""
    return await _safe_compat_call(
        "browser_file_upload",
        browser_compat.browser_file_upload,
        paths=paths,
    )


@_if_compat
async def browser_tabs(action: str, index: Optional[int] = None) -> dict:
    """Manage tabs in the shared compat browser session."""
    return await _safe_compat_call(
        "browser_tabs",
        browser_compat.browser_tabs,
        action=action,
        index=index,
    )


@_if_compat
async def browser_close() -> dict:
    """Close the shared compat browser session."""
    return await _safe_compat_call("browser_close", browser_compat.browser_close)


@_if_compat
async def browser_console_messages(
    level: str = "info",
    all_messages: bool = False,
    all_compat: Optional[bool] = None,
) -> dict:
    """Read console messages captured in the shared compat browser session."""
    if all_compat is not None:
        all_messages = all_compat
    return await _safe_compat_call(
        "browser_console_messages",
        browser_compat.browser_console_messages,
        level=level,
        all_messages=all_messages,
    )


@_if_compat
async def browser_network_requests(
    include_static: bool = False,
    includeStatic: Optional[bool] = None,
) -> dict:
    """List network requests captured in the shared compat browser session."""
    if includeStatic is not None:
        include_static = includeStatic
    return await _safe_compat_call(
        "browser_network_requests",
        browser_compat.browser_network_requests,
        include_static=include_static,
    )


@_if_compat
async def browser_take_screenshot(
    filename: Optional[str] = None,
    full_page: bool = False,
    ref: Optional[str] = None,
    selector: Optional[str] = None,
    image_type: str = "png",
    fullPage: Optional[bool] = None,
    img_type: Optional[str] = None,
    type: Optional[str] = None,
) -> dict:
    """Take a screenshot from the shared compat browser session."""
    if fullPage is not None:
        full_page = fullPage
    if img_type is not None:
        image_type = img_type
    if type is not None:
        image_type = type
    return await _safe_compat_call(
        "browser_take_screenshot",
        browser_compat.browser_take_screenshot,
        filename=filename,
        full_page=full_page,
        ref=ref,
        selector=selector,
        img_type=image_type,
    )


@_if_compat
async def browser_wait_for(
    time: Optional[float] = None,
    text: Optional[str] = None,
    text_gone: Optional[str] = None,
    textGone: Optional[str] = None,
) -> dict:
    """Wait for a condition (time/text/text_gone) in shared compat browser session."""
    if textGone is not None:
        text_gone = textGone
    return await _safe_compat_call(
        "browser_wait_for",
        browser_compat.browser_wait_for,
        time_secs=time,
        text=text,
        text_gone=text_gone,
    )


@_if_compat
async def browser_press_key(key: str) -> dict:
    """Send a keyboard key press in the shared compat browser session."""
    return await _safe_compat_call("browser_press_key", browser_compat.browser_press_key, key=key)


@_if_compat
async def browser_resize(width: int, height: int) -> dict:
    """Resize the shared compat browser viewport."""
    return await _safe_compat_call("browser_resize", browser_compat.browser_resize, width=width, height=height)


@_if_compat
async def browser_handle_dialog(
    accept: bool = True,
    prompt_text: Optional[str] = None,
    promptText: Optional[str] = None,
) -> dict:
    """Configure how the next JS dialog (alert/confirm/prompt) is handled."""
    if promptText is not None:
        prompt_text = promptText
    return await _safe_compat_call(
        "browser_handle_dialog",
        browser_compat.browser_handle_dialog,
        accept=accept,
        prompt_text=prompt_text,
    )


@_if_compat
async def browser_route(
    pattern: str,
    status: int = 200,
    body: Optional[str] = None,
    content_type: Optional[str] = None,
    headers: Optional[list[str]] = None,
    contentType: Optional[str] = None,
) -> dict:
    """Mock network routes in the shared compat browser session (not regression mocks)."""
    if contentType is not None:
        content_type = contentType
    payload = await _safe_compat_call(
        "browser_route",
        browser_compat.browser_route,
        pattern=pattern,
        status=status,
        body=body,
        content_type=content_type,
        headers=headers,
    )
    headers_dict = {}
    for item in headers or []:
        if ":" not in item:
            continue
        k, v = item.split(":", 1)
        headers_dict[k.strip()] = v.strip()
    return _add_deprecation_notice(
        payload,
        replacement_tool="route_register",
        replacement_payload={
            "scope": "compat_session",
            "pattern": pattern,
            "action": "fulfill",
            "status": status,
            "body": body,
            "content_type": content_type,
            "headers": headers_dict or None,
        },
    )


@_if_compat
async def browser_unroute(pattern: Optional[str] = None) -> dict:
    """Remove one route mock (or all) from the shared compat browser session."""
    payload = await _safe_compat_call(
        "browser_unroute",
        browser_compat.browser_unroute,
        pattern=pattern,
    )
    return _add_deprecation_notice(
        payload,
        replacement_tool="route_clear",
        replacement_payload={
            "scope": "compat_session",
            "pattern": pattern,
        },
    )


@_if_compat
async def browser_route_list() -> dict:
    """List active network route mocks in the shared compat browser session."""
    payload = await _safe_compat_call("browser_route_list", browser_compat.browser_route_list)
    return _add_deprecation_notice(
        payload,
        replacement_tool="route_list",
        replacement_payload={"scope": "compat_session"},
    )


@_if_compat
async def browser_network_state_set(state: str) -> dict:
    """Set emulated network condition for the shared compat browser session."""
    return await _safe_compat_call(
        "browser_network_state_set",
        browser_compat.browser_network_state_set,
        state=state,
    )


@_if_compat
async def browser_cookie_list(domain: Optional[str] = None, path: Optional[str] = None) -> dict:
    """List cookies from the shared compat browser session."""
    payload = await _safe_compat_call(
        "browser_cookie_list",
        browser_compat.browser_cookie_list,
        domain=domain,
        path=path,
    )
    return _add_deprecation_notice(
        payload,
        replacement_tool="storage_get",
        replacement_payload={
            "scope": "compat_session",
            "resource": "cookies",
            "domain": domain,
            "path": path,
            "include_values": True,
        },
    )


@_if_compat
async def browser_cookie_get(name: str) -> dict:
    """Get a single cookie by name from the shared compat browser session."""
    payload = await _safe_compat_call(
        "browser_cookie_get",
        browser_compat.browser_cookie_get,
        name=name,
    )
    return _add_deprecation_notice(
        payload,
        replacement_tool="storage_get",
        replacement_payload={
            "scope": "compat_session",
            "resource": "cookies",
            "name": name,
            "include_values": True,
        },
    )


@_if_compat
async def browser_cookie_set(
    name: str,
    value: str,
    domain: Optional[str] = None,
    path: str = "/",
    expires: Optional[float] = None,
    http_only: bool = True,
    secure: bool = True,
    same_site: Optional[str] = None,
    httpOnly: Optional[bool] = None,
    sameSite: Optional[str] = None,
) -> dict:
    """Set a cookie in the shared compat browser session."""
    if httpOnly is not None:
        http_only = httpOnly
    if sameSite is not None:
        same_site = sameSite
    payload = await _safe_compat_call(
        "browser_cookie_set",
        browser_compat.browser_cookie_set,
        name=name,
        value=value,
        domain=domain,
        path=path,
        expires=expires,
        http_only=http_only,
        secure=secure,
        same_site=same_site,
    )
    return _add_deprecation_notice(
        payload,
        replacement_tool="storage_set",
        replacement_payload={
            "scope": "compat_session",
            "resource": "cookies",
            "operation": "upsert",
            "cookie": {
                "name": name,
                "value": value,
                "domain": domain,
                "path": path,
                "expires": expires,
                "httpOnly": http_only,
                "secure": secure,
                "sameSite": same_site,
            },
        },
    )


@_if_compat
async def browser_cookie_delete(name: str) -> dict:
    """Delete a cookie by name in the shared compat browser session."""
    payload = await _safe_compat_call(
        "browser_cookie_delete",
        browser_compat.browser_cookie_delete,
        name=name,
    )
    return _add_deprecation_notice(
        payload,
        replacement_tool="storage_set",
        replacement_payload={
            "scope": "compat_session",
            "resource": "cookies",
            "operation": "delete",
            "name": name,
        },
    )


@_if_compat
async def browser_cookie_clear() -> dict:
    """Clear all cookies in the shared compat browser session."""
    payload = await _safe_compat_call("browser_cookie_clear", browser_compat.browser_cookie_clear)
    return _add_deprecation_notice(
        payload,
        replacement_tool="storage_set",
        replacement_payload={
            "scope": "compat_session",
            "resource": "cookies",
            "operation": "clear",
        },
    )


@_if_compat
async def browser_storage_state(filename: Optional[str] = None) -> dict:
    """Save storage state for the shared compat browser session."""
    payload = await _safe_compat_call(
        "browser_storage_state",
        browser_compat.browser_storage_state,
        filename=filename,
    )
    return _add_deprecation_notice(
        payload,
        replacement_tool="storage_export",
        replacement_payload={
            "scope": "compat_session",
            "filename": filename,
        },
    )


@_if_compat
async def browser_set_storage_state(filename: str) -> dict:
    """Restore storage state into the shared compat browser session."""
    payload = await _safe_compat_call(
        "browser_set_storage_state",
        browser_compat.browser_set_storage_state,
        filename=filename,
    )
    return _add_deprecation_notice(
        payload,
        replacement_tool="storage_import",
        replacement_payload={
            "scope": "compat_session",
            "filename": filename,
            "merge": False,
        },
    )


@_if_compat
async def browser_localstorage_list() -> dict:
    """List localStorage entries in the shared compat browser session."""
    payload = await _safe_compat_call(
        "browser_localstorage_list",
        browser_compat.browser_localstorage_list,
    )
    return _add_deprecation_notice(
        payload,
        replacement_tool="storage_get",
        replacement_payload={"scope": "compat_session", "resource": "local_storage"},
    )


@_if_compat
async def browser_localstorage_get(key: str) -> dict:
    """Get one localStorage value in the shared compat browser session."""
    payload = await _safe_compat_call(
        "browser_localstorage_get",
        browser_compat.browser_localstorage_get,
        key=key,
    )
    return _add_deprecation_notice(
        payload,
        replacement_tool="storage_get",
        replacement_payload={"scope": "compat_session", "resource": "local_storage", "key": key},
    )


@_if_compat
async def browser_localstorage_set(key: str, value: str) -> dict:
    """Set one localStorage value in the shared compat browser session."""
    payload = await _safe_compat_call(
        "browser_localstorage_set",
        browser_compat.browser_localstorage_set,
        key=key,
        value=value,
    )
    return _add_deprecation_notice(
        payload,
        replacement_tool="storage_set",
        replacement_payload={
            "scope": "compat_session",
            "resource": "local_storage",
            "operation": "upsert",
            "key": key,
            "value": value,
        },
    )


@_if_compat
async def browser_localstorage_delete(key: str) -> dict:
    """Delete one localStorage value in the shared compat browser session."""
    payload = await _safe_compat_call(
        "browser_localstorage_delete",
        browser_compat.browser_localstorage_delete,
        key=key,
    )
    return _add_deprecation_notice(
        payload,
        replacement_tool="storage_set",
        replacement_payload={
            "scope": "compat_session",
            "resource": "local_storage",
            "operation": "delete",
            "key": key,
        },
    )


@_if_compat
async def browser_localstorage_clear() -> dict:
    """Clear localStorage in the shared compat browser session."""
    payload = await _safe_compat_call(
        "browser_localstorage_clear",
        browser_compat.browser_localstorage_clear,
    )
    return _add_deprecation_notice(
        payload,
        replacement_tool="storage_set",
        replacement_payload={
            "scope": "compat_session",
            "resource": "local_storage",
            "operation": "clear",
        },
    )


@_if_compat
async def browser_sessionstorage_list() -> dict:
    """List sessionStorage entries in the shared compat browser session."""
    payload = await _safe_compat_call(
        "browser_sessionstorage_list",
        browser_compat.browser_sessionstorage_list,
    )
    return _add_deprecation_notice(
        payload,
        replacement_tool="storage_get",
        replacement_payload={"scope": "compat_session", "resource": "session_storage"},
    )


@_if_compat
async def browser_sessionstorage_get(key: str) -> dict:
    """Get one sessionStorage value in the shared compat browser session."""
    payload = await _safe_compat_call(
        "browser_sessionstorage_get",
        browser_compat.browser_sessionstorage_get,
        key=key,
    )
    return _add_deprecation_notice(
        payload,
        replacement_tool="storage_get",
        replacement_payload={"scope": "compat_session", "resource": "session_storage", "key": key},
    )


@_if_compat
async def browser_sessionstorage_set(key: str, value: str) -> dict:
    """Set one sessionStorage value in the shared compat browser session."""
    payload = await _safe_compat_call(
        "browser_sessionstorage_set",
        browser_compat.browser_sessionstorage_set,
        key=key,
        value=value,
    )
    return _add_deprecation_notice(
        payload,
        replacement_tool="storage_set",
        replacement_payload={
            "scope": "compat_session",
            "resource": "session_storage",
            "operation": "upsert",
            "key": key,
            "value": value,
        },
    )


@_if_compat
async def browser_sessionstorage_delete(key: str) -> dict:
    """Delete one sessionStorage value in the shared compat browser session."""
    payload = await _safe_compat_call(
        "browser_sessionstorage_delete",
        browser_compat.browser_sessionstorage_delete,
        key=key,
    )
    return _add_deprecation_notice(
        payload,
        replacement_tool="storage_set",
        replacement_payload={
            "scope": "compat_session",
            "resource": "session_storage",
            "operation": "delete",
            "key": key,
        },
    )


@_if_compat
async def browser_sessionstorage_clear() -> dict:
    """Clear sessionStorage in the shared compat browser session."""
    payload = await _safe_compat_call(
        "browser_sessionstorage_clear",
        browser_compat.browser_sessionstorage_clear,
    )
    return _add_deprecation_notice(
        payload,
        replacement_tool="storage_set",
        replacement_payload={
            "scope": "compat_session",
            "resource": "session_storage",
            "operation": "clear",
        },
    )


@mcp.tool()
async def cancel_run(run_id: str) -> dict:
    """Cancel a running test and mark it as cancelled.

    Args:
        run_id: The run_id to cancel

    Returns:
        dict with run_id, previous_status, new_status
    """

    async def _cancel_handler() -> dict:
        run = await sqlite.get_run(run_id)
        if not run:
            return tool_error(f"Run {run_id} not found", BLOP_RUN_NOT_FOUND, details={"run_id": run_id})
        prev_status = run.get("status", "unknown")
        if prev_status in ("completed", "failed", "cancelled", "interrupted"):
            return {
                "run_id": run_id,
                "previous_status": prev_status,
                "new_status": prev_status,
                "task_cancelled": False,
                "note": "Run already terminated",
            }
        # Mark cancelled in the DB before cancelling the task so done-callbacks do not
        # race and overwrite user cancel with "interrupted".
        await sqlite.update_run_status(run_id, "cancelled")
        task_cancelled = regression.cancel_run_task(run_id)
        return {
            "run_id": run_id,
            "previous_status": prev_status,
            "new_status": "cancelled",
            "task_cancelled": task_cancelled,
        }

    return await _safe_call(_cancel_handler)


@mcp.tool()
async def record_test_flow(
    flow_name: str,
    goal: str,
    app_url: Optional[str] = None,
    profile_name: Optional[str] = None,
    command: Optional[str] = None,
    business_criticality: Optional[str] = "other",
) -> dict:
    """Record a test flow by running a Browser-Use agent to accomplish a goal.

    Captures each action with selector, target_text, dom_fingerprint, per-step
    screenshots, and generates final assertion steps from a Gemini screenshot analysis.

    Args:
        flow_name: Short name for this flow (used as identifier)
        goal: Plain-English description of what to accomplish
        app_url: Website URL (or mobile package/bundle id when platform is ios/android).
            May be omitted when APP_BASE_URL is set in the server environment (web only).
        profile_name: Optional auth profile name (from save_auth_profile)
        command: Optional natural language command for additional context
        business_criticality: "revenue" | "activation" | "retention" | "support" | "other"

    Returns:
        dict with flow_id, flow_name, step_count, status, artifacts_dir
    """
    return await _safe_call(
        record.record_test_flow,
        flow_name=flow_name,
        goal=goal,
        app_url=app_url,
        profile_name=profile_name,
        command=command,
        business_criticality=business_criticality or "other",
    )


@mcp.tool()
async def package_authenticated_saas_baseline(
    baseline_name: str,
    recipes: list[dict],
    app_url: Optional[str] = None,
    profile_name: Optional[str] = None,
) -> dict:
    """Package reusable authenticated SaaS goldens into strict-step release-gate flows.

    Use this after discovery or live exploration when you know the stable semantic path
    you want to gate on. It promotes curated recipes into recorded flows that replay in
    strict_steps mode and are ready for run_release_check(mode="replay").

    Supported recipe_type values:
    - role_click_to_url
    - role_click_to_text
    - selector_then_role_to_url
    - text_click_to_text
    - text_then_text_to_text
    - text_then_selector_to_text
    """
    return await _safe_call(
        baselines_tools.package_authenticated_saas_baseline,
        tool_name="package_authenticated_saas_baseline",
        baseline_name=baseline_name,
        recipes=recipes,
        app_url=app_url,
        profile_name=profile_name,
    )


@_if_legacy
async def run_regression_test(
    app_url: str,
    flow_ids: list[str],
    profile_name: Optional[str] = None,
    headless: bool = True,
    run_mode: str = "hybrid",
    command: Optional[str] = None,
    auto_rerecord: bool = False,
) -> dict:
    """[DEPRECATED — use run_release_check for release decisions] Run regression tests against recorded flows. Returns immediately; poll get_test_results for status.

    Uses hybrid step-by-step replay by default: tries saved selectors first, falls back
    to text-based lookup, then repairs individual broken steps via vision LLM.

    Self-healing: When steps are repaired successfully, healed selectors are persisted
    back into the recorded flow for future runs. When auto_rerecord is True and a flow
    fails completely, blop will attempt to re-record the flow from its original goal.

    Args:
        app_url: The website URL to test against
        flow_ids: List of flow_id strings from record_test_flow
        profile_name: Optional auth profile name
        headless: Run browsers headlessly (default: True)
        run_mode: "hybrid" (default), "strict_steps", or "goal_fallback" (accepts legacy alias "strict")
        command: Optional natural language command for additional context
        auto_rerecord: If True, attempt to re-record flows that fail completely (hard heal)

    Returns:
        dict with run_id, status ("running"), flow_count, artifacts_dir
    """
    return await _safe_call(
        regression.run_regression_test,
        app_url=app_url,
        flow_ids=flow_ids,
        profile_name=profile_name,
        headless=headless,
        run_mode=run_mode,
        command=command,
        auto_rerecord=auto_rerecord,
    )


@_if_compat
async def compare_visual_baseline(
    flow_id: str,
    step_index: Optional[int] = None,
) -> dict:
    """Compare visual baseline screenshots for a recorded flow.

    Args:
        flow_id: The flow_id to check baselines for
        step_index: Optional specific step index to check (returns all if omitted)

    Returns:
        dict with baseline info or error
    """
    try:
        from blop.engine.visual_regression import compare_visual_baseline as _compare

        return await _compare(flow_id, step_index)
    except Exception as e:
        return tool_error(str(e), BLOP_MCP_INTERNAL_TOOL_ERROR)


@mcp.tool()
async def get_test_results(run_id: str) -> dict:
    """Get structured results for a test run.

    Prefer run_release_check + blop://release/{release_id}/brief for release gating.
    This tool remains the detailed run-level payload, now with summary-first fields.

    Args:
        run_id: The run_id returned from run_regression_test

    Returns:
        dict with run_id, status, cases (with assertion_results, replay_mode_used,
        step_failure_index, artifact_paths), severity_counts, failed_cases, next_actions
    """
    return await _safe_call(results.get_test_results, run_id=run_id)


@mcp.tool()
async def get_process_insights(run_id: str, include_pm4py: bool = True) -> dict:
    """Derive process-mining style variants from run health events (optional PM4Py when installed).

    Uses replay_step_completed and other health events. Install ``blop-mcp[insights]`` for PM4Py stats.
    """
    return await _safe_call(
        process_insights_tools.get_process_insights,
        tool_name="get_process_insights",
        run_id=run_id,
        include_pm4py=include_pm4py,
    )


@mcp.tool()
async def export_run_trace(run_id: str) -> dict:
    """Export OTLP-shaped JSON (resourceSpans) for a run — local SQLite only, no network upload."""
    return await _safe_call(
        process_insights_tools.export_run_trace_otel,
        tool_name="export_run_trace",
        run_id=run_id,
    )


@_if_compat
async def list_runs(limit: int = 20, status: Optional[str] = None) -> dict:
    """List recent regression runs, optionally filtered by status.

    Args:
        limit: Number of runs to return (default 20, max 200)
        status: Optional status filter (
            "queued", "running", "waiting_auth", "completed", "failed", "cancelled", "interrupted"
        )
    """
    return await _safe_call(results.list_runs, limit=limit, status=status)


@_if_compat
async def get_run_health_stream(run_id: str, limit: int = 500) -> dict:
    """Get control-plane health events for a run (queue/start/case/complete/fail)."""
    return await _safe_call(results.get_run_health_stream, run_id=run_id, limit=limit)


@_if_compat
async def get_risk_analytics(limit_runs: int = 30) -> dict:
    """[DEPRECATED — use blop://release/{id}/incidents or v2 resources] Aggregate flaky-step, transition-failure, and business-critical risk analytics."""
    return await _safe_call(results.get_risk_analytics, limit_runs=limit_runs)


@_if_compat
async def list_recorded_tests() -> dict:
    """[DEPRECATED — use blop://journeys resource for canonical planning context] List all recorded test flows.

    Returns:
        dict with flows (list of {flow_id, flow_name, app_url, goal, created_at}), total
    """
    try:
        from blop.schemas import RecordedTestsResult
        from blop.storage.sqlite import list_flows

        flows = await list_flows()
        return RecordedTestsResult(flows=flows, total=len(flows)).model_dump()
    except Exception as e:
        return tool_error(str(e), BLOP_MCP_INTERNAL_TOOL_ERROR)


@mcp.tool()
async def debug_test_case(run_id: str, case_id: str) -> dict:
    """Re-run a failed test case in headed mode with verbose evidence capture.

    Shows the exact step that failed, repair attempt results, per-step screenshots,
    and a plain-English "why this failed" explanation with concrete next actions.

    Args:
        run_id: The run_id containing the failure
        case_id: The case_id of the specific failure to debug

    Returns:
        dict with case_id, run_id, status, screenshots, console_log, repro_steps,
        step_failure_index, replay_mode, assertion_failures, why_failed
    """
    try:
        return await debug.debug_test_case(run_id, case_id)
    except Exception as e:
        return tool_error(str(e), BLOP_MCP_INTERNAL_TOOL_ERROR)


@_if_legacy
async def validate_setup(
    app_url: Optional[str] = None,
    profile_name: Optional[str] = None,
    check_mobile: bool = False,
) -> dict:
    """[DEPRECATED — use validate_release_setup] Check all preconditions before running tests.

    Verifies: GOOGLE_API_KEY, Chromium installation, SQLite DB access,
    optional app_url reachability, and optional auth profile validity.

    Args:
        app_url: Optional URL to check reachability
        profile_name: Optional auth profile name to validate
        check_mobile: If True, also checks Appium server reachability for mobile testing

    Returns:
        dict with status ("ready" | "warnings" | "blocked"), checks, blockers, warnings
    """
    try:
        return await validate.validate_setup(
            app_url=app_url,
            profile_name=profile_name,
            check_mobile=check_mobile,
        )
    except Exception as e:
        return tool_error(str(e), BLOP_MCP_INTERNAL_TOOL_ERROR)


# ---------------------------------------------------------------------------
# Structured Assertion Tools — lightweight standalone verifications
# ---------------------------------------------------------------------------


@_if_compat
async def verify_element_visible(
    app_url: str,
    role: str,
    accessible_name: str,
    profile_name: Optional[str] = None,
) -> dict:
    """Verify that an ARIA element with the given role and accessible name is visible.

    Args:
        app_url: The URL to navigate to
        role: ARIA role (e.g. "button", "link", "textbox")
        accessible_name: The accessible name of the element
        profile_name: Optional auth profile for protected pages
    """
    try:
        return await assertions_tools.verify_element_visible(app_url, role, accessible_name, profile_name)
    except Exception as e:
        return tool_error(str(e), BLOP_MCP_INTERNAL_TOOL_ERROR)


@_if_compat
async def verify_text_visible(
    app_url: str,
    text: str,
    profile_name: Optional[str] = None,
) -> dict:
    """Verify that specific text content is present on a page.

    Args:
        app_url: The URL to navigate to
        text: The text to look for
        profile_name: Optional auth profile for protected pages
    """
    try:
        return await assertions_tools.verify_text_visible(app_url, text, profile_name)
    except Exception as e:
        return tool_error(str(e), BLOP_MCP_INTERNAL_TOOL_ERROR)


@_if_compat
async def verify_value(
    app_url: str,
    selector: str,
    expected_value: str,
    profile_name: Optional[str] = None,
) -> dict:
    """Verify that a form field has the expected value.

    Args:
        app_url: The URL to navigate to
        selector: CSS selector of the form field
        expected_value: Expected input value
        profile_name: Optional auth profile for protected pages
    """
    try:
        return await assertions_tools.verify_value(app_url, selector, expected_value, profile_name)
    except Exception as e:
        return tool_error(str(e), BLOP_MCP_INTERNAL_TOOL_ERROR)


@_if_compat
async def verify_visual_state(
    app_url: str,
    description: str,
    profile_name: Optional[str] = None,
) -> dict:
    """Verify a visual condition on a page using vision LLM analysis.

    Args:
        app_url: The URL to navigate to
        description: Natural-language description of the expected visual state
        profile_name: Optional auth profile for protected pages
    """
    try:
        return await assertions_tools.verify_visual_state(app_url, description, profile_name)
    except Exception as e:
        return tool_error(str(e), BLOP_MCP_INTERNAL_TOOL_ERROR)


@_if_compat
async def verify_semantic_query(
    app_url: str,
    query: str,
    expected: Optional[str] = None,
    profile_name: Optional[str] = None,
    target_selector: Optional[str] = None,
    target_role: Optional[str] = None,
    target_name: Optional[str] = None,
) -> dict:
    """Verify a semantic assertion against the current page using accessibility/DOM extraction first."""
    try:
        return await assertions_tools.verify_semantic_query(
            app_url,
            query,
            expected,
            profile_name,
            target_selector,
            target_role,
            target_name,
        )
    except Exception as e:
        return tool_error(str(e), BLOP_MCP_INTERNAL_TOOL_ERROR)


@_if_compat
async def export_test_report(
    run_id: str,
    format: str = "markdown",
) -> dict:
    """Export a test run report in markdown, HTML, or JSON format.

    Args:
        run_id: The run_id to export
        format: "markdown" (default), "html", or "json"

    Returns:
        dict with format, path, case_count
    """
    try:
        from blop.reporting.export import export_test_report as _export

        return await _export(run_id, format)
    except Exception as e:
        return tool_error(str(e), BLOP_MCP_INTERNAL_TOOL_ERROR)


# ---------------------------------------------------------------------------
# Security Scanning Tools
# ---------------------------------------------------------------------------


@_if_compat
async def security_scan(
    repo_path: str,
    scan_type: str = "semgrep",
    ruleset: str = "p/default",
    severity_filter: Optional[str] = None,
) -> dict:
    """Run a static security scan (SAST) on a codebase using Semgrep.

    Semgrep must be installed separately (pip install semgrep).

    Args:
        repo_path: Path to the directory to scan
        scan_type: "semgrep" (only supported type currently)
        ruleset: Semgrep ruleset to use (default: "p/default")
        severity_filter: Optional filter: "ERROR", "WARNING", or "INFO"

    Returns:
        dict with findings, each containing rule_id, severity, file, line, CWE, fix suggestion
    """
    try:
        return await security_tools.security_scan(repo_path, scan_type, ruleset, severity_filter)
    except Exception as e:
        return tool_error(str(e), BLOP_MCP_INTERNAL_TOOL_ERROR)


@_if_compat
async def security_scan_url(
    app_url: str,
    scan_type: str = "headers",
) -> dict:
    """Analyze HTTP security headers for a live URL.

    Checks for HSTS, CSP, X-Frame-Options, X-Content-Type-Options, Referrer-Policy, etc.

    Args:
        app_url: The URL to scan
        scan_type: "headers" (only supported type currently)

    Returns:
        dict with security_score (0-1), headers present/missing, and recommendations
    """
    try:
        return await security_tools.security_scan_url(app_url, scan_type)
    except Exception as e:
        return tool_error(str(e), BLOP_MCP_INTERNAL_TOOL_ERROR)


# ---------------------------------------------------------------------------
# Canonical Routing + Legacy Network Mocking Tools
# ---------------------------------------------------------------------------


@_if_compat
async def route_register(
    scope: Literal["compat_session", "regression_replay"],
    pattern: str,
    action: Literal["fulfill", "abort", "continue"] = "fulfill",
    status: int = 200,
    body: Optional[str] = None,
    content_type: str = "application/json",
    headers: Optional[dict[str, str]] = None,
    times: Optional[int] = None,
    name: Optional[str] = None,
    run_id: Optional[str] = None,
) -> dict:
    """Register a scoped network route mock."""
    return await _safe_call(
        network_tools.route_register,
        scope=scope,
        pattern=pattern,
        action=action,
        status=status,
        body=body,
        content_type=content_type,
        headers=headers,
        times=times,
        name=name,
        run_id=run_id,
    )


@_if_compat
async def route_list(
    scope: Literal["compat_session", "regression_replay"],
    run_id: Optional[str] = None,
) -> dict:
    """List active route mocks for a scope."""
    return await _safe_call(network_tools.route_list, scope=scope, run_id=run_id)


@_if_compat
async def route_clear(
    scope: Literal["compat_session", "regression_replay"],
    pattern: Optional[str] = None,
    name: Optional[str] = None,
    run_id: Optional[str] = None,
) -> dict:
    """Clear active route mocks for a scope."""
    return await _safe_call(
        network_tools.route_clear,
        scope=scope,
        pattern=pattern,
        name=name,
        run_id=run_id,
    )


@_if_compat
async def mock_network_route(
    pattern: str,
    status: int = 200,
    body: Optional[str] = None,
    content_type: str = "application/json",
) -> dict:
    """Deprecated wrapper for regression replay route mocks."""
    payload = await _safe_call(
        network_tools.route_register,
        scope="regression_replay",
        pattern=pattern,
        action="fulfill",
        status=status,
        body=body,
        content_type=content_type,
    )
    return _add_deprecation_notice(
        payload,
        replacement_tool="route_register",
        replacement_payload={
            "scope": "regression_replay",
            "pattern": pattern,
            "action": "fulfill",
            "status": status,
            "body": body,
            "content_type": content_type,
        },
    )


@_if_compat
async def clear_network_routes() -> dict:
    """Deprecated wrapper for clearing regression replay route mocks."""
    payload = await _safe_call(
        network_tools.route_clear,
        scope="regression_replay",
    )
    return _add_deprecation_notice(
        payload,
        replacement_tool="route_clear",
        replacement_payload={"scope": "regression_replay"},
    )


# ---------------------------------------------------------------------------
# Code Generation Tool
# ---------------------------------------------------------------------------


@_if_compat
async def export_flow_as_code(
    flow_id: str,
    language: str = "python",
) -> dict:
    """Export a recorded test flow as a standalone Playwright test script.

    Converts the recorded FlowSteps into runnable code using semantic locators
    (ARIA role+name, testid, label) captured at record time.

    Args:
        flow_id: The flow_id to export
        language: "python" (default) or "typescript"

    Returns:
        dict with flow_id, language, path to generated file, step_count
    """
    from blop.engine.codegen import export_flow_as_code as _export

    return await _safe_call(_export, flow_id=flow_id, language=language)


# ---------------------------------------------------------------------------
# Canonical Storage + Legacy Storage State Management Tools
# ---------------------------------------------------------------------------


@_if_compat
async def storage_get(
    scope: Literal["profile_url", "compat_session", "regression_replay"],
    resource: Literal["cookies", "local_storage", "session_storage", "all"] = "cookies",
    app_url: Optional[str] = None,
    profile_name: Optional[str] = None,
    run_id: Optional[str] = None,
    name: Optional[str] = None,
    key: Optional[str] = None,
    domain: Optional[str] = None,
    path: Optional[str] = None,
    include_values: bool = False,
) -> dict:
    """Read scoped browser cookies/localStorage/sessionStorage."""
    return await _safe_call(
        storage_tools.storage_get,
        scope=scope,
        resource=resource,
        app_url=app_url,
        profile_name=profile_name,
        run_id=run_id,
        name=name,
        key=key,
        domain=domain,
        path=path,
        include_values=include_values,
    )


@_if_compat
async def storage_set(
    scope: Literal["profile_url", "compat_session", "regression_replay"],
    resource: Literal["cookies", "local_storage", "session_storage", "all"] = "cookies",
    operation: Literal["upsert", "delete", "clear"] = "upsert",
    app_url: Optional[str] = None,
    profile_name: Optional[str] = None,
    run_id: Optional[str] = None,
    cookie: Optional[dict] = None,
    key: Optional[str] = None,
    value: Optional[str] = None,
    name: Optional[str] = None,
    domain: Optional[str] = None,
    path: str = "/",
    persist: bool = True,
) -> dict:
    """Mutate scoped browser cookies/localStorage/sessionStorage."""
    return await _safe_call(
        storage_tools.storage_set,
        scope=scope,
        resource=resource,
        operation=operation,
        app_url=app_url,
        profile_name=profile_name,
        run_id=run_id,
        cookie=cookie,
        key=key,
        value=value,
        name=name,
        domain=domain,
        path=path,
        persist=persist,
    )


@_if_compat
async def storage_export(
    scope: Literal["profile_url", "compat_session", "regression_replay"],
    app_url: Optional[str] = None,
    profile_name: Optional[str] = None,
    run_id: Optional[str] = None,
    filename: Optional[str] = None,
    include_cookies: bool = True,
    include_local_storage: bool = True,
    include_session_storage: bool = True,
) -> dict:
    """Export scoped browser storage state."""
    return await _safe_call(
        storage_tools.storage_export,
        scope=scope,
        app_url=app_url,
        profile_name=profile_name,
        run_id=run_id,
        filename=filename,
        include_cookies=include_cookies,
        include_local_storage=include_local_storage,
        include_session_storage=include_session_storage,
    )


@_if_compat
async def storage_import(
    scope: Literal["profile_url", "compat_session", "regression_replay"],
    filename: str,
    app_url: Optional[str] = None,
    profile_name: Optional[str] = None,
    run_id: Optional[str] = None,
    merge: bool = False,
) -> dict:
    """Import scoped browser storage state."""
    return await _safe_call(
        storage_tools.storage_import,
        scope=scope,
        filename=filename,
        app_url=app_url,
        profile_name=profile_name,
        run_id=run_id,
        merge=merge,
    )


@_if_compat
async def get_browser_cookies(
    app_url: str,
    profile_name: Optional[str] = None,
) -> dict:
    """Deprecated wrapper for URL-scoped cookie listing."""
    payload = await _safe_call(
        storage_tools.storage_get,
        scope="profile_url",
        resource="cookies",
        app_url=app_url,
        profile_name=profile_name,
        include_values=False,
    )
    return _add_deprecation_notice(
        payload,
        replacement_tool="storage_get",
        replacement_payload={
            "scope": "profile_url",
            "resource": "cookies",
            "app_url": app_url,
            "profile_name": profile_name,
        },
    )


@_if_compat
async def set_browser_cookie(
    app_url: str,
    name: str,
    value: str,
    domain: Optional[str] = None,
    path: str = "/",
    secure: bool = False,
    http_only: bool = False,
    profile_name: Optional[str] = None,
) -> dict:
    """Deprecated wrapper for URL-scoped cookie writes."""
    payload = await _safe_call(
        storage_tools.storage_set,
        scope="profile_url",
        resource="cookies",
        operation="upsert",
        app_url=app_url,
        profile_name=profile_name,
        cookie={
            "name": name,
            "value": value,
            "domain": domain,
            "path": path,
            "secure": secure,
            "httpOnly": http_only,
        },
        persist=True,
    )
    return _add_deprecation_notice(
        payload,
        replacement_tool="storage_set",
        replacement_payload={
            "scope": "profile_url",
            "resource": "cookies",
            "operation": "upsert",
            "app_url": app_url,
            "profile_name": profile_name,
            "cookie": {
                "name": name,
                "value": value,
                "domain": domain,
                "path": path,
                "secure": secure,
                "httpOnly": http_only,
            },
            "persist": True,
        },
    )


@_if_compat
async def save_browser_state(
    app_url: str,
    profile_name: Optional[str] = None,
    filename: Optional[str] = None,
) -> dict:
    """Deprecated wrapper for URL-scoped storage export."""
    payload = await _safe_call(
        storage_tools.storage_export,
        scope="profile_url",
        app_url=app_url,
        profile_name=profile_name,
        filename=filename,
    )
    return _add_deprecation_notice(
        payload,
        replacement_tool="storage_export",
        replacement_payload={
            "scope": "profile_url",
            "app_url": app_url,
            "profile_name": profile_name,
            "filename": filename,
        },
    )


# ---------------------------------------------------------------------------
# MCP v2 Tools — change intelligence + reliability control plane
# ---------------------------------------------------------------------------


@_if_compat
async def blop_v2_get_surface_contract() -> dict:
    """Get v2 MCP tool schemas and request examples."""
    return await _safe_call(v2_surface.get_surface_contract)


@_if_compat
async def blop_v2_capture_context(
    app_url: str,
    profile_name: Optional[str] = None,
    repo_path: Optional[str] = None,
    max_depth: int = 2,
    max_pages: int = BLOP_DISCOVERY_MAX_PAGES,
    seed_urls: Optional[list[str]] = None,
    include_url_pattern: Optional[str] = None,
    exclude_url_pattern: Optional[str] = None,
    intent_focus: Optional[list[str]] = None,
) -> dict:
    """Capture/persist a context graph snapshot with diff summary."""
    return await _safe_call(
        v2_surface.capture_context,
        app_url=app_url,
        profile_name=profile_name,
        repo_path=repo_path,
        max_depth=max_depth,
        max_pages=max_pages,
        seed_urls=seed_urls,
        include_url_pattern=include_url_pattern,
        exclude_url_pattern=exclude_url_pattern,
        intent_focus=intent_focus,
    )


@_if_compat
async def blop_v2_compare_context(
    app_url: str,
    baseline_graph_id: str,
    candidate_graph_id: str,
    impact_lens: Optional[list[str]] = None,
) -> dict:
    """Compare two context graph versions and return impact summary."""
    return await _safe_call(
        v2_surface.compare_context,
        app_url=app_url,
        baseline_graph_id=baseline_graph_id,
        candidate_graph_id=candidate_graph_id,
        impact_lens=impact_lens,
    )


@_if_compat
async def blop_v2_assess_release_risk(
    app_url: str,
    release_id: Optional[str] = None,
    baseline_ref: Optional[ReleaseReference | dict] = None,
    candidate_ref: Optional[ReleaseReference | dict] = None,
    criticality_weights: Optional[dict] = None,
) -> dict:
    """Assess release risk from context diff + run outcomes."""
    return await _safe_call(
        v2_surface.assess_release_risk,
        app_url=app_url,
        release_id=release_id,
        baseline_ref=baseline_ref,
        candidate_ref=candidate_ref,
        criticality_weights=criticality_weights,
    )


@_if_compat
async def blop_v2_get_journey_health(
    app_url: str,
    window: str = "7d",
    journey_filter: Optional[list[str]] = None,
    criticality_filter: Optional[list[str]] = None,
) -> dict:
    """Get SLO-like health for key journeys across a time window."""
    return await _safe_call(
        v2_surface.get_journey_health,
        app_url=app_url,
        window=window,
        journey_filter=journey_filter,
        criticality_filter=criticality_filter,
    )


@_if_compat
async def blop_v2_cluster_incidents(
    app_url: str,
    run_ids: Optional[list[str]] = None,
    window: str = "7d",
    min_cluster_size: int = 2,
) -> dict:
    """Cluster failures into deduplicated incidents with blast radius."""
    return await _safe_call(
        v2_surface.cluster_incidents,
        app_url=app_url,
        run_ids=run_ids,
        window=window,
        min_cluster_size=min_cluster_size,
    )


@_if_compat
async def blop_v2_generate_remediation(
    cluster_id: str,
    format: str = "markdown",
    include_owner_hints: bool = True,
    include_fix_hypotheses: bool = True,
) -> dict:
    """Generate an action-ready remediation draft for an incident cluster."""
    return await _safe_call(
        v2_surface.generate_remediation,
        cluster_id=cluster_id,
        format=format,
        include_owner_hints=include_owner_hints,
        include_fix_hypotheses=include_fix_hypotheses,
    )


@_if_compat
async def blop_v2_ingest_telemetry_signals(
    app_url: str,
    signals: list[TelemetrySignalInput | dict],
    source: str = "custom",
) -> dict:
    """Ingest external telemetry for correlation against incidents."""
    return await _safe_call(
        v2_surface.ingest_telemetry_signals,
        app_url=app_url,
        signals=signals,
        source=source,
    )


@_if_compat
async def blop_v2_get_correlation_report(
    app_url: str,
    window: str = "7d",
    min_confidence: float = 0.6,
) -> dict:
    """Correlate incident clusters with telemetry signals."""
    return await _safe_call(
        v2_surface.get_correlation_report,
        app_url=app_url,
        window=window,
        min_confidence=min_confidence,
    )


@_if_compat
async def blop_v2_suggest_flows_for_diff(
    app_url: str,
    changed_files: list[str],
    changed_routes: Optional[list[str]] = None,
    limit: int = 5,
) -> dict:
    """Suggest which recorded test flows to run based on changed files/routes.

    Uses the context graph to find intent nodes connected to routes whose path segments
    overlap with the changed file paths. Useful for CI/CD to run only relevant tests.

    Args:
        app_url: The app URL (must have an existing context graph from blop_v2_capture_context)
        changed_files: List of changed file paths (e.g. ["src/checkout/index.tsx"])
        changed_routes: Optional list of changed URL routes to also factor in
        limit: Maximum number of flow suggestions to return (default 5)

    Returns:
        dict with app_url, changed_segments_detected, suggested_flow_ids, suggestions[]
    """
    return await _safe_call(
        v2_surface.suggest_flows_for_diff,
        app_url=app_url,
        changed_files=changed_files,
        changed_routes=changed_routes,
        limit=limit,
    )


@_if_compat
async def blop_v2_autogenerate_flows(
    app_url: str,
    profile_name: Optional[str] = None,
    criticality_filter: Optional[list[str]] = None,
    auto_record: bool = False,
    limit: int = 5,
    record: Optional[bool] = None,
) -> dict:
    """Auto-generate test flow specs from context graph intents that lack recorded flows.

    Finds intent nodes in the context graph that don't have a matching recorded flow,
    synthesizes flow specs from the intent metadata, and optionally records them.

    Args:
        app_url: The app URL (must have an existing context graph)
        profile_name: Optional auth profile for recording
        criticality_filter: Optional list of criticality levels to include (e.g. ["revenue", "activation"])
        auto_record: If True, call record_test_flow for each synthesized flow
        limit: Maximum number of flows to synthesize (default 5)

    Returns:
        dict with app_url, synthesized[], recorded_flow_ids[], total_unmatched_intents
    """
    if record is not None:
        auto_record = record
    return await _safe_call(
        v2_surface.autogenerate_flows,
        app_url=app_url,
        profile_name=profile_name,
        criticality_filter=criticality_filter,
        auto_record=auto_record,
        limit=limit,
    )


@_if_compat
async def blop_v2_archive_storage(
    older_than_days: int = 30,
    keep_failed: bool = True,
    archive_telemetry: bool = False,
    telemetry_older_than_days: int = 90,
) -> dict:
    """Archive old runs, cases, artifacts, and optionally telemetry signals.

    Args:
        older_than_days: Delete runs older than this many days (default 30)
        keep_failed: If True, retain failed runs regardless of age (default True)
        archive_telemetry: If True, also archive old telemetry signals (default False)
        telemetry_older_than_days: Telemetry age cutoff in days (default 90)

    Returns:
        dict with archived_runs, cutoff, kept_failed (+ telemetry stats if archive_telemetry)
    """
    try:
        from blop.storage.sqlite import archive_old_runs, archive_old_telemetry

        result = await archive_old_runs(older_than_days=older_than_days, keep_failed=keep_failed)
        if archive_telemetry:
            tel_result = await archive_old_telemetry(older_than_days=telemetry_older_than_days)
            result["telemetry"] = tel_result
        return result
    except Exception as e:
        return tool_error(str(e), BLOP_MCP_INTERNAL_TOOL_ERROR)


# ---------------------------------------------------------------------------
# MCP Resources — read-only context for low-token agent planning
# ---------------------------------------------------------------------------


@mcp.resource("blop://health")
async def health_resource() -> dict:
    """Server health check: DB reachability, LLM key, Chromium, active run count."""
    import shutil
    from datetime import datetime, timezone

    from blop.config import check_llm_api_key, runtime_posture_snapshot
    from blop.storage import files as file_store

    has_key, _key_name = check_llm_api_key()
    chromium_ok = bool(shutil.which("chromium") or shutil.which("chromium-browser") or shutil.which("google-chrome"))
    db_ok = False
    try:
        await sqlite.list_runs(limit=1)
        db_ok = True
    except Exception:
        pass
    active_runs = sum(1 for t in regression._RUN_TASKS.values() if not t.done())
    posture = runtime_posture_snapshot()
    path_checks = {
        "runs_dir_resolves_absolute": file_store._runs_dir().is_absolute(),
        "debug_log_parent_configured": bool(posture["paths"]["debug_log"]),
        "db_path_absolute": posture["paths"]["db_path_absolute"],
    }
    return {
        "status": "ready" if (has_key and db_ok) else "degraded",
        "db_reachable": db_ok,
        "llm_key_present": has_key,
        "chromium_found": chromium_ok,
        "active_run_count": active_runs,
        "runtime_posture": posture,
        "path_checks": path_checks,
        "ts": datetime.now(timezone.utc).isoformat(),
    }


@mcp.resource("blop://inventory/{app}")
async def inventory_resource(app: str) -> dict:
    """Latest crawl inventory for an app URL (URL-encoded in resource URI)."""
    app_url = unquote(app)
    return await discover.get_inventory_resource(app_url)


@mcp.resource("blop://context-graph/{app}")
async def context_graph_resource(app: str) -> dict:
    """Latest context graph snapshot for an app URL (URL-encoded in resource URI)."""
    app_url = unquote(app)
    return await discover.get_context_graph_resource(app_url)


@mcp.resource("blop://run/{run_id}/artifact-index")
async def run_artifact_index_resource(run_id: str) -> dict:
    """Read-only artifact index for a run."""
    return await results.get_artifact_index_resource(run_id)


@mcp.resource("blop://runs/{run_id}")
async def run_status_resource_handler(run_id: str) -> dict:
    """Current run state for reconnect recovery — status, flow_count, poll_recipe or brief link."""
    from blop.tools import resources as _resources_mod

    return await _resources_mod.run_status_resource(run_id)


@mcp.resource("blop://run/{run_id}/artifacts")
async def run_artifacts_alias_resource(run_id: str) -> dict:
    """Alias of blop://run/{run_id}/artifact-index for cloud/client parity."""
    return await results.get_artifact_index_resource(run_id)


@mcp.resource("blop://run/{run_id}/mobile_artifacts")
async def run_mobile_artifacts_resource(run_id: str) -> dict:
    """Mobile replay evidence: screenshots, page_source paths, device logs (per run case)."""
    from blop.tools import resources as _resources_mod

    return await _resources_mod.run_mobile_artifacts_resource(run_id)


@mcp.resource("blop://run/{run_id}/recommendation")
async def run_recommendation_resource(run_id: str) -> dict:
    """Release recommendation (SHIP / INVESTIGATE / BLOCK) for a completed run. Lightweight polling target — cheaper than get_test_results."""
    return await results.get_run_recommendation_resource(run_id)


@mcp.resource("blop://flow/{flow_id}/stability-profile")
async def flow_stability_profile_resource(flow_id: str) -> dict:
    """Read-only stability profile for a recorded flow."""
    return await results.get_flow_stability_profile_resource(flow_id)


@mcp.resource("blop://prompts/list")
async def prompts_list_resource() -> dict:
    """Debug/internal resource: list available prompt templates with previews."""
    from blop.prompts import list_available_prompts

    return list_available_prompts()


@mcp.resource("blop://prompts/{name}")
async def prompt_resource(name: str) -> dict:
    """Debug/internal resource: read a specific engine prompt template by name (discover, repair, remediation, next_actions)."""
    from blop.prompts import DISCOVER_PROMPT, NEXT_ACTIONS_PROMPT, REMEDIATION_PROMPT, REPAIR_STEP_PROMPT, get_prompt

    defaults = {
        "discover": DISCOVER_PROMPT,
        "repair": REPAIR_STEP_PROMPT,
        "remediation": REMEDIATION_PROMPT,
        "next_actions": NEXT_ACTIONS_PROMPT,
    }
    default = defaults.get(name, "")
    prompt = get_prompt(name, default)
    return {"name": name, "prompt": prompt, "is_override": prompt != default}


@mcp.resource("blop://v2/contracts/tools")
async def v2_contracts_resource() -> dict:
    """V2 MCP tool contracts: request/response schemas + examples."""
    return await v2_surface.get_surface_contract()


@mcp.resource("blop://v2/context/{app}/latest")
async def v2_context_latest_resource(app: str) -> dict:
    """Latest v2 context graph summary for URL-encoded app URL."""
    app_url = unquote(app)
    return await v2_surface.get_context_latest_resource(app_url)


@mcp.resource("blop://v2/context/{app}/history/{limit}")
async def v2_context_history_resource(app: str, limit: str) -> dict:
    """Context graph history with explicit limit path segment."""
    app_url = unquote(app)
    safe_limit = 20
    try:
        safe_limit = max(1, min(int(limit), 100))
    except Exception:
        pass
    return await v2_surface.get_context_history_resource(app_url=app_url, limit=safe_limit)


@mcp.resource("blop://v2/context/{app}/diff/{baseline_graph_id}/{candidate_graph_id}")
async def v2_context_diff_resource(app: str, baseline_graph_id: str, candidate_graph_id: str) -> dict:
    """Context graph structural/business diff between two versions."""
    app_url = unquote(app)
    return await v2_surface.get_context_diff_resource(
        app_url=app_url,
        baseline_graph_id=baseline_graph_id,
        candidate_graph_id=candidate_graph_id,
    )


@mcp.resource("blop://v2/release/{release_id}/risk-summary")
async def v2_release_risk_resource(release_id: str) -> dict:
    """Release risk summary snapshot by release_id."""
    return await v2_surface.get_release_risk_resource(release_id)


@mcp.resource("blop://v2/journey/{app}/health/{window}")
async def v2_journey_health_resource(app: str, window: str) -> dict:
    """Journey health resource for URL-encoded app and time window."""
    app_url = unquote(app)
    safe_window = window if window in ("24h", "7d", "30d") else "7d"
    return await v2_surface.get_journey_health_resource(app_url=app_url, window=safe_window)


@mcp.resource("blop://v2/incidents/{app}/open")
async def v2_incidents_open_resource(app: str) -> dict:
    """Open incident clusters for URL-encoded app URL."""
    app_url = unquote(app)
    return await v2_surface.get_incidents_open_resource(app_url=app_url)


@mcp.resource("blop://v2/incident/{cluster_id}")
async def v2_incident_resource(cluster_id: str) -> dict:
    """Single incident cluster record."""
    return await v2_surface.get_incident_resource(cluster_id=cluster_id)


@mcp.resource("blop://v2/incident/{cluster_id}/remediation-draft")
async def v2_incident_remediation_resource(cluster_id: str) -> dict:
    """Remediation draft for incident cluster."""
    return await v2_surface.get_incident_remediation_resource(cluster_id=cluster_id)


@mcp.resource("blop://v2/correlation/{app}/{window}")
async def v2_correlation_resource(app: str, window: str) -> dict:
    """Latest correlation report for URL-encoded app URL and window."""
    app_url = unquote(app)
    safe_window = window if window in ("24h", "7d", "30d") else "7d"
    return await v2_surface.get_correlation_resource(app_url=app_url, window=safe_window)


# ---------------------------------------------------------------------------
# MCP Prompts — surface workflow starting points in Claude Code / Cursor
# ---------------------------------------------------------------------------


@mcp.prompt()
def discover_critical_flows() -> str:
    return """First run validate_release_setup to confirm your environment is ready:
  validate_release_setup(app_url="https://your-app.com")

Then discover the most important journeys using business language:
  discover_critical_journeys(
    app_url="https://your-app.com",
    business_goal="Find the 5 most revenue-critical flows including signup, onboarding, and billing."
  )

Journeys with include_in_release_gating=true (revenue, activation) are automatically
prioritized for release checks. Record those first:
  record_test_flow(
    app_url="https://your-app.com",
    flow_name="checkout_flow",
    goal="Complete a purchase end-to-end",
    business_criticality="revenue"
  )

Then run a release check across all gated journeys:
  run_release_check(app_url="https://your-app.com")"""


@mcp.prompt()
def setup_auth() -> str:
    return """To test authenticated flows, save an auth profile first.

Choose the auth_type that matches your app:

1. env_login — agent logs in with credentials from environment variables:
   save_auth_profile(
     profile_name="staging",
     auth_type="env_login",
     login_url="https://your-app.com/login",
     username_env="TEST_USERNAME",
     password_env="TEST_PASSWORD"
   )
   Then set: export TEST_USERNAME=user@example.com && export TEST_PASSWORD=secret

2. storage_state — replay a Playwright session file:
   save_auth_profile(
     profile_name="staging",
     auth_type="storage_state",
     storage_state_path="/path/to/storage_state.json"
   )

3. cookie_json — inject raw cookies:
   save_auth_profile(
     profile_name="staging",
     auth_type="cookie_json",
     cookie_json_path="/path/to/cookies.json"
   )

4. capture_auth_session — interactive capture for Google/GitHub OAuth or any MFA flow:
   capture_auth_session(
     profile_name="myapp",
     login_url="https://app.example.com/login",
     success_url_pattern="/dashboard",
     timeout_secs=120
   )
   A browser window opens — complete OAuth/MFA manually — state is saved automatically.
   Use user_data_dir for OAuth providers that detect fresh browser contexts as bots:
   capture_auth_session(
     profile_name="myapp",
     login_url="https://app.example.com/login",
     success_url_pattern="/dashboard",
     user_data_dir=".blop/chrome_profile_myapp"
   )

After saving, pass profile_name to record_test_flow and run_regression_test."""


@mcp.prompt()
def run_smoke_regression() -> str:
    return """To run a release confidence check against critical journeys:

1. List available flows:
   list_recorded_tests()

2. Run release check (returns immediately — poll for results):
   run_release_check(
     app_url="https://your-app.com",
     profile_name="staging"  # optional
   )
   The status will be "queued" → "running" → "completed"

3. Poll for results (repeat until status is "completed" or "failed"):
   get_test_results(run_id="<run_id>")

4. Triage any blockers:
   triage_release_blocker(run_id="<run_id>")

The report includes a SHIP / INVESTIGATE / BLOCK decision with blocker journeys
labeled by business criticality so you can triage at a glance."""


@mcp.prompt()
def record_flow_with_structure() -> str:
    return """To record a robust flow with better navigation context:

1. Map the interface first:
   explore_site_inventory(
     app_url="https://your-app.com",
     max_depth=2,
     max_pages=20
   )

2. (Optional) Capture one-page structure right before recording:
   get_page_structure(
     app_url="https://your-app.com",
     url="https://your-app.com/settings",
     profile_name="staging"  # optional
   )

3. Record using concrete goals informed by discovered routes/buttons/forms:
   record_test_flow(
     app_url="https://your-app.com",
     flow_name="update_profile",
     goal="Open settings, update profile name, save changes, and verify success toast appears",
     profile_name="staging"
   )

Use structure context from step 1-2 to avoid guessing selectors and to choose the right starting route."""


@mcp.prompt()
def debug_failed_case() -> str:
    return """To investigate a specific test failure:

1. Get the run results to find the failed case:
   get_test_results(run_id="<run_id>")

   Look for cases with status "fail" or "error". Note the case_id.

2. Re-run in headed mode with full evidence capture:
   debug_test_case(run_id="<run_id>", case_id="<case_id>")

   This replays the flow with a visible browser, captures per-step screenshots,
   console logs, and a plain-English "why this failed" explanation with 3 fix suggestions.

3. If the failure is an auth issue (status "waiting_auth"):
   - Check your auth profile: validate_setup(profile_name="<profile_name>")
   - Re-save with correct credentials: save_auth_profile(...)
   - Then retry: run_regression_test(...)"""


@mcp.prompt()
def context_first_discovery() -> str:
    return """Use this context-first workflow to minimize tokens and improve discovery quality:

1) Read resources first:
   - blop://inventory/{urlencoded_app_url}
   - blop://context-graph/{urlencoded_app_url}

2) If resources are missing, generate them:
   - explore_site_inventory(app_url="https://your-app.com", max_depth=2, max_pages=20)
   - discover_test_flows(app_url="https://your-app.com", return_inventory=true)

3) Re-read resources, then prioritize recording flows with:
   - business_criticality in ["revenue", "activation"]
   - highest confidence
   - strongest alignment with business goal.
"""


@mcp.prompt()
def context_guided_regression() -> str:
    return """Use resources + tools together for faster triage:

1) Run release check:
   run_release_check(app_url="https://your-app.com", run_mode="hybrid")

2) Poll:
   get_test_results(run_id="<run_id>")

3) Triage blockers:
   triage_release_blocker(run_id="<run_id>")

4) Read release resources:
   - blop://release/<release_id>/brief
   - blop://release/<release_id>/artifacts
   - blop://release/<release_id>/incidents

5) Use those resources to prioritize:
   - blocker/high failures in revenue or activation flows
   - low stability_score flows
   - repeated failure hotspots (same step_failure_index)
"""


@mcp.prompt()
def observability_control_plane() -> str:
    return """Use blop control-plane observability for triage:

1) Pull latest status:
   get_test_results(run_id="<run_id>")

2) Inspect event stream:
   get_run_health_stream(run_id="<run_id>")

3) Evaluate fleet risk:
   get_risk_analytics(limit_runs=30)

4) Prioritize fixes by:
   - revenue/activation failure_rate
   - top flaky step keys
   - top failing transitions
"""


@mcp.prompt()
def quick_web_eval() -> str:
    return """Quickly evaluate a web app with a single tool call:

1. (Optional) If auth is required, save a session first:
   capture_auth_session(
     login_url="https://your-app.com/login",
     profile_name="myapp",
     success_url_pattern="/dashboard"
   )

2. Evaluate the app with a natural-language task:
   evaluate_web_task(
     task="Try the full signup flow and report any UX issues",
     app_url="https://your-app.com",  # optional if APP_BASE_URL is set
     profile_name="myapp"  # optional
   )

   This returns a complete report with screenshots, console errors, network
   failures, and agent step timeline — no need to discover or record flows first.

3. If the evaluation looks good, promote it to a regression test:
   evaluate_web_task(
     task="Complete checkout with a test product",
     app_url="https://your-app.com",
     save_as_recorded_flow=True,
     flow_name="checkout_flow"
   )

   The recorded flow can then be replayed with run_regression_test."""


# ===========================================================================
# CANONICAL MVP SURFACE — Release Confidence Control Plane
# ===========================================================================
# Primary API: release tools + blop:// resources + context/atomic tools (see docs/MCP_CORE_WORKFLOW.md).
#
# Backbone (context-first): load_context → select_journeys → plan → navigate & act → observe
#   → record_evidence → summarize
#
# Typical release gate:
#   validate_release_setup → discover_critical_journeys → record_test_flow → run_release_check(mode="replay") → triage_release_blocker
#
# Extra tools (evaluate_web_task, etc.) register above; discover_test_flows / run_regression_test /
# validate_setup register only when BLOP_ENABLE_LEGACY_MCP_TOOLS=true.
# Playwright-compat browser_* and blop_v2_* register only when BLOP_ENABLE_COMPAT_TOOLS=true.
# ===========================================================================

from blop.mcp.envelope import ok_response as _ok_tool_response


@mcp.tool()
async def get_mcp_capabilities() -> dict:
    """O(1) probe: package version, surface flags, registered tool count, and canonical tool names.

    Use this or the ``blop://health`` resource before heavier discovery or replay work.
    """
    from importlib import metadata

    from blop.config import BLOP_ENABLE_COMPAT_TOOLS, BLOP_ENABLE_LEGACY_MCP_TOOLS

    try:
        pkg_version = metadata.version("blop-mcp")
    except metadata.PackageNotFoundError:
        pkg_version = "unknown"
    tm = mcp._tool_manager
    reg = getattr(tm, "_tools", {}) or {}
    data = {
        "package_version": pkg_version,
        "registered_tool_count": len(reg),
        "legacy_mcp_tools_enabled": BLOP_ENABLE_LEGACY_MCP_TOOLS,
        "compat_tools_enabled": BLOP_ENABLE_COMPAT_TOOLS,
        "health_resource_uri": "blop://health",
        "canonical_release_tools": [
            "validate_release_setup",
            "discover_critical_journeys",
            "record_test_flow",
            "run_release_check",
            "triage_release_blocker",
            "get_qa_recommendations",
        ],
        "context_tools": [
            "get_workspace_context",
            "get_release_context",
            "get_release_and_journeys",
            "get_journeys_for_release",
        ],
    }
    return _ok_tool_response(data).model_dump()


@mcp.tool()
async def validate_release_setup(
    app_url: Optional[str] = None,
    profile_name: Optional[str] = None,
    check_mobile: bool = False,
) -> dict:
    """Preflight check before a release: verifies API key, Chromium, DB, app reachability, and auth profile.

    This is the canonical MVP entry point — run this before discover_critical_journeys or run_release_check.

    Args:
        check_mobile: If True, also checks Appium server reachability for mobile testing.
    """
    return await _safe_call(
        validate.validate_release_setup,
        tool_name="validate_release_setup",
        app_url=app_url,
        profile_name=profile_name,
        check_mobile=check_mobile,
    )


@mcp.tool()
async def discover_critical_journeys(
    app_url: Optional[str] = None,
    profile_name: Optional[str] = None,
    business_goal: Optional[str] = None,
    max_depth: int = 2,
    max_pages: int = BLOP_DISCOVERY_MAX_PAGES,
    seed_urls: Optional[list[str]] = None,
    include_url_pattern: Optional[str] = None,
    exclude_url_pattern: Optional[str] = None,
) -> dict:
    """Crawl app_url and plan 3-8 critical user journeys in business language.

    Returns CriticalJourney objects with why_it_matters and include_in_release_gating fields
    so you can immediately scope which journeys gate a release. Revenue and activation journeys
    are automatically flagged for release gating.

    app_url may be omitted when APP_BASE_URL (or BLOP_APP_URL) is set in the server environment.
    """
    return await _safe_call(
        journeys_tools.discover_critical_journeys,
        tool_name="discover_critical_journeys",
        app_url=app_url,
        profile_name=profile_name,
        business_goal=business_goal,
        max_depth=max_depth,
        max_pages=max_pages,
        seed_urls=seed_urls,
        include_url_pattern=include_url_pattern,
        exclude_url_pattern=exclude_url_pattern,
    )


@mcp.tool()
async def run_release_check(
    app_url: Optional[str] = None,
    journey_ids: Optional[list[str]] = None,
    flow_ids: Optional[list[str]] = None,
    profile_name: Optional[str] = None,
    mode: Literal["replay", "targeted"] = "replay",
    criticality_filter: Optional[list[str]] = None,
    release_id: Optional[str] = None,
    headless: bool = True,
    run_mode: str = "hybrid",
    smoke_preflight: bool = False,
) -> dict:
    """Flagship release confidence tool: replay critical journeys and return a SHIP / INVESTIGATE / BLOCK decision.

    In replay mode (default), queues a regression run and returns immediately with run_id for polling.
    In targeted mode, runs a one-shot agent evaluation synchronously as a shortcut smoke check.

    Args:
        app_url: Web URL or mobile package/bundle id. May be omitted when APP_BASE_URL is set (web only).
        journey_ids: deprecated alias for flow_ids.
        flow_ids: recorded flow IDs to replay. If omitted, uses all flows matching criticality_filter.
        criticality_filter: defaults to ["revenue", "activation"].
        release_id: optional caller-supplied release identifier (auto-generated if omitted).
        mode: "replay" (default, golden path for release gating) or "targeted" (one-shot eval).
        smoke_preflight: Optional advisory smoke sweep before replay. Does not block the release on its own.
    """
    return await _safe_call(
        release_check_tools.run_release_check,
        tool_name="run_release_check",
        app_url=app_url,
        journey_ids=journey_ids,
        flow_ids=flow_ids,
        profile_name=profile_name,
        mode=mode,
        criticality_filter=criticality_filter,
        release_id=release_id,
        headless=headless,
        run_mode=run_mode,
        smoke_preflight=smoke_preflight,
    )


@mcp.tool()
async def triage_release_blocker(
    run_id: Optional[str] = None,
    release_id: Optional[str] = None,
    flow_id: Optional[str] = None,
    journey_id: Optional[str] = None,
    incident_cluster_id: Optional[str] = None,
    generate_remediation: bool = True,
) -> dict:
    """Root-cause evidence + next actions for a release blocker.

    Accepts any of: run_id, release_id, flow_id, journey_id, incident_cluster_id (at least one required).
    Returns BlockerTriage with likely_cause, evidence_summary, user_business_impact,
    recommended_action, suggested_owner, and linked_artifacts.
    """
    return await _safe_call(
        triage_tools.triage_release_blocker,
        tool_name="triage_release_blocker",
        run_id=run_id,
        release_id=release_id,
        flow_id=flow_id,
        journey_id=journey_id,
        incident_cluster_id=incident_cluster_id,
        generate_remediation=generate_remediation,
    )


@mcp.tool()
async def get_qa_recommendations(
    app_url: Optional[str] = None,
    release_id: Optional[str] = None,
    scope: Literal["full", "blockers_only", "coverage_gaps"] = "full",
    lookback_runs: int = 10,
) -> dict:
    """QA-engineering view: test pyramid health, coverage gaps, flakiness signals, and prioritized recommendations.

    Aggregates recorded journeys and recent run cases for app_url, then returns a RecommendationSet plus
    embedded qa_context (risk matrix, defect mix, pyramid stats). Use scope to narrow the recommendation lists.

    app_url may be omitted when APP_BASE_URL (or BLOP_APP_URL) is set in the server environment.
    """
    return await _safe_call(
        qa_advisor_tools.get_qa_recommendations,
        tool_name="get_qa_recommendations",
        app_url=app_url,
        release_id=release_id,
        scope=scope,
        lookback_runs=lookback_runs,
    )


# ── Context-first tools (agent-ergonomic JSON; see docs/MCP_CORE_WORKFLOW.md) ─


@mcp.tool()
async def get_workspace_context() -> dict:
    """Return compact workspace metadata, resource URIs, and discovery defaults."""
    return await _safe_call(context_read_tools.get_workspace_context, tool_name="get_workspace_context")


@mcp.tool()
async def get_release_context(release_id: str) -> dict:
    """Return structured release brief (decision, risk, blockers) for a release_id."""
    return await _safe_call(
        context_read_tools.get_release_context,
        tool_name="get_release_context",
        release_id=release_id,
    )


@mcp.tool()
async def get_journeys_for_release(
    release_id: Optional[str] = None,
    app_url: Optional[str] = None,
) -> dict:
    """List recorded journeys filtered by release brief app_url or explicit app_url."""
    return await _safe_call(
        context_read_tools.get_journeys_for_release,
        tool_name="get_journeys_for_release",
        release_id=release_id,
        app_url=app_url,
    )


@mcp.tool()
async def get_release_and_journeys(release_id: str) -> dict:
    """Batch: release context plus journeys for the release app URL in one call."""
    return await _safe_call(
        context_read_tools.get_release_and_journeys,
        tool_name="get_release_and_journeys",
        release_id=release_id,
    )


@mcp.tool()
async def get_prd_and_acceptance_criteria(
    journey_id: Optional[str] = None,
    release_id: Optional[str] = None,
) -> dict:
    """Summaries and acceptance-style criteria from recorded flows / release brief (no external PRD yet)."""
    return await _safe_call(
        context_read_tools.get_prd_and_acceptance_criteria,
        tool_name="get_prd_and_acceptance_criteria",
        journey_id=journey_id,
        release_id=release_id,
    )


@mcp.tool()
async def get_ux_taxonomy() -> dict:
    """Static UX/criticality hints for planning (cached, small JSON)."""
    return await _safe_call(context_read_tools.get_ux_taxonomy, tool_name="get_ux_taxonomy")


# ── Atomic browser tools (shared Playwright session) ──────────────────────────


@mcp.tool()
async def navigate_to_url(url: str, profile_name: Optional[str] = None) -> dict:
    """Navigate the shared browser session to a URL (ok/data envelope)."""
    return await _safe_call(
        atomic_browser_tools.navigate_to_url,
        tool_name="navigate_to_url",
        url=url,
        profile_name=profile_name,
    )


@mcp.tool()
async def navigate_to_journey(journey_id: str, profile_name: Optional[str] = None) -> dict:
    """Open a recorded journey's entry URL (flow_id == journey_id)."""
    return await _safe_call(
        atomic_browser_tools.navigate_to_journey,
        tool_name="navigate_to_journey",
        journey_id=journey_id,
        profile_name=profile_name,
    )


@mcp.tool()
async def get_page_snapshot(selector: Optional[str] = None, filename: Optional[str] = None) -> dict:
    """Compact interactive listing from the Playwright accessibility tree (legacy name; prefer get_page_state)."""
    return await _safe_call(
        atomic_browser_tools.get_page_snapshot,
        tool_name="get_page_snapshot",
        selector=selector,
        filename=filename,
    )


@mcp.tool()
async def get_page_state(include_markdown: bool = True, max_nodes: int = 250) -> dict:
    """Structured a11y-first page state: roles, names, refs, optional markdown (primary observation surface)."""
    return await _safe_call(
        atomic_browser_tools.get_page_state,
        tool_name="get_page_state",
        include_markdown=include_markdown,
        max_nodes=max_nodes,
    )


@mcp.tool()
async def perform_step(step_spec: dict) -> dict:
    """One structured step: click | type | wait | press_key | navigate (see PerformStepSpec)."""
    return await _safe_call(
        atomic_browser_tools.perform_step,
        tool_name="perform_step",
        step_spec=step_spec,
    )


@mcp.tool()
async def capture_artifact(kind: str, metadata: Optional[dict] = None) -> dict:
    """Capture screenshot, dom_snapshot, or network_log; optional run_id routes under runs/."""
    return await _safe_call(
        atomic_browser_tools.capture_artifact,
        tool_name="capture_artifact",
        kind=kind,
        metadata=metadata,
    )


@mcp.tool()
async def record_run_observation(
    run_id: str,
    observation_key: str,
    observation_payload: dict,
) -> dict:
    """Idempotent agent observation keyed by (run_id, observation_key)."""
    return await _safe_call(
        atomic_browser_tools.record_run_observation,
        tool_name="record_run_observation",
        run_id=run_id,
        observation_key=observation_key,
        observation_payload=observation_payload,
    )


# ── MVP RESOURCES ─────────────────────────────────────────────────────────────


@mcp.resource("blop://journeys")
async def journeys_resource() -> dict:
    """All recorded journeys as CriticalJourney-shaped objects."""
    return await resources_tools.journeys_resource()


@mcp.resource("blop://release/{release_id}/brief")
async def release_brief_resource(release_id: str) -> dict:
    """Condensed release summary: decision, risk score, blocker count, top actions."""
    return await resources_tools.release_brief_resource(release_id)


@mcp.resource("blop://release/{release_id}/artifacts")
async def release_artifacts_resource(release_id: str) -> dict:
    """Screenshots, traces, and console logs for a release run, grouped by type."""
    return await resources_tools.release_artifacts_resource(release_id)


@mcp.resource("blop://release/{release_id}/incidents")
async def release_incidents_resource(release_id: str) -> dict:
    """Incident clusters linked to a release run."""
    return await resources_tools.release_incidents_resource(release_id)


# ── MVP PROMPTS ───────────────────────────────────────────────────────────────


@mcp.prompt()
def release_readiness_review() -> str:
    return prompts_tools.RELEASE_READINESS_REVIEW


@mcp.prompt()
def investigate_blocker() -> str:
    return prompts_tools.INVESTIGATE_BLOCKER


@mcp.prompt()
def explain_release_risk() -> str:
    return prompts_tools.EXPLAIN_RELEASE_RISK


def run() -> int:
    """Entry point for the MCP server."""
    import asyncio
    import signal

    from blop.config import (
        BLOP_API_TOKEN,
        BLOP_DB_PATH,
        BLOP_DEBUG_LOG,
        BLOP_HOSTED_URL,
        BLOP_PROJECT_ID,
        check_llm_api_key,
        cloud_sync_missing_vars,
        runtime_config_issues,
    )
    from blop.storage import files as file_store

    def _check_writable(path: Path, *, as_file: bool = False) -> str | None:
        try:
            target_dir = path.parent if as_file else path
            target_dir.mkdir(parents=True, exist_ok=True)
            probe = target_dir / ".blop_write_test"
            probe.touch()
            probe.unlink(missing_ok=True)
            return None
        except Exception as exc:
            return f"{path}: {exc}"

    cfg_errors, cfg_warnings = runtime_config_issues()
    if cfg_warnings:
        for warning in cfg_warnings:
            _log.warning("startup_config_warning warning=%s", warning)

    db_error = _check_writable(Path(BLOP_DB_PATH), as_file=True)
    if db_error:
        cfg_errors.append(f"BLOP_DB_PATH is not writable: {db_error}")

    runs_error = _check_writable(file_store._runs_dir(), as_file=False)
    if runs_error:
        cfg_errors.append(f"BLOP_RUNS_DIR is not writable: {runs_error}")

    if BLOP_DEBUG_LOG:
        log_error = _check_writable(Path(BLOP_DEBUG_LOG), as_file=True)
        if log_error:
            cfg_errors.append(f"BLOP_DEBUG_LOG is not writable: {log_error}")

    if cfg_errors:
        for err in cfg_errors:
            _log.error("startup_config_error error=%s", err)
        return 1

    asyncio.run(sqlite.init_db())

    # Warn if cloud sync is partially configured (only some vars set)
    _sync_vars_set = [v for v in [BLOP_HOSTED_URL, BLOP_API_TOKEN, BLOP_PROJECT_ID] if v]
    if 0 < len(_sync_vars_set) < 3:
        _log.warning(
            "startup event=partial_cloud_sync_config "
            "Partial cloud sync config detected. Runs will NOT sync to Blop Cloud. "
            "Missing: %s",
            cloud_sync_missing_vars(),
        )

    try:
        resume_summary = asyncio.run(regression.resume_incomplete_runs())
        if resume_summary.get("resumed", 0):
            _log.info(
                "startup event=resumed_runs resumed=%s eligible=%s waiting_auth=%s skipped=%s",
                resume_summary.get("resumed", 0),
                resume_summary.get("eligible", 0),
                resume_summary.get("waiting_auth", 0),
                resume_summary.get("skipped", 0),
            )
    except Exception as e:
        _log.warning("startup_resume_runs_failed error=%s", e)

    # Startup validation warning (non-blocking)
    has_key, key_name = check_llm_api_key()
    if not has_key:
        _log.warning("startup event=missing_llm_key key_name=%s", key_name)

    # Signal handlers for graceful shutdown (single-process deployment)
    def _handle_signal(signum, frame):
        _log.info("shutdown event=signal signum=%s", signum)
        raise SystemExit(0)

    signal.signal(signal.SIGTERM, _handle_signal)
    # SIGINT already triggers KeyboardInterrupt → SystemExit; leave as-is

    _log.info("startup event=server_ready")
    exit_code = 0
    try:
        mcp.run()
    except (SystemExit, KeyboardInterrupt):
        pass  # clean exit
    except Exception as e:
        _log.error("server_crash error=%s", e, exc_info=True)
        exit_code = 1
    finally:
        drained = {"cancelled": 0, "timed_out": 0, "forced": 0}
        forced = {"forced_cancelled": 0}
        try:
            drained = asyncio.run(regression.shutdown_run_tasks())
        except Exception as e:
            _log.warning("shutdown_drain_failed error=%s", e)
        try:
            forced = asyncio.run(regression.force_finalize_active_runs(reason="server_shutdown"))
            _log.info(
                "shutdown event=tasks_cleaned cancelled=%s timed_out=%s forced=%s forced_cancelled=%s",
                drained.get("cancelled", 0),
                drained.get("timed_out", 0),
                drained.get("forced", 0),
                forced.get("forced_cancelled", 0),
            )
        except Exception as e:
            _log.warning("shutdown_cleanup_failed error=%s", e)
    return exit_code


if __name__ == "__main__":
    run()
