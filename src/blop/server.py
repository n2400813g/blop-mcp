import logging
import os
from urllib.parse import unquote

# Must happen before any other imports to prevent JSON-RPC interference
logging.disable(logging.CRITICAL)
os.environ.setdefault("ANONYMIZED_TELEMETRY", "false")
os.environ.setdefault("BROWSER_USE_LOGGING_LEVEL", "CRITICAL")

from typing import Optional

from mcp.server.fastmcp import FastMCP

from blop.config import BLOP_DISCOVERY_MAX_PAGES
from blop.storage import sqlite
from blop.storage.sqlite import init_db
from blop.tools import assertions as assertions_tools
from blop.tools import auth, capture_auth, debug, discover, evaluate as evaluate_tools
from blop.tools import network as network_tools
from blop.tools import record, regression, results, security as security_tools
from blop.tools import storage as storage_tools, v2_surface, validate

mcp = FastMCP("blop")


@mcp.tool()
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
    """Discover test flows for an application by scanning its pages or source code.

    Uses a BFS crawl to extract page signals (CTAs, auth routes, forms, headings),
    then sends them to Gemini to generate 5-8 meaningful test flows with severity hints.

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
    try:
        return await discover.discover_test_flows(
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
    except Exception as e:
        return {"error": str(e)}


@mcp.tool()
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
    try:
        return await discover.explore_site_inventory(
            app_url=app_url,
            profile_name=profile_name,
            max_depth=max_depth,
            max_pages=max_pages,
            seed_urls=seed_urls,
            include_url_pattern=include_url_pattern,
            exclude_url_pattern=exclude_url_pattern,
        )
    except Exception as e:
        return {"error": str(e)}


@mcp.tool()
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
    try:
        return await discover.get_page_structure(
            app_url=app_url,
            url=url,
            profile_name=profile_name,
        )
    except Exception as e:
        return {"error": str(e)}


@mcp.tool()
async def save_auth_profile(
    profile_name: str,
    auth_type: str,
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
    try:
        return await auth.save_auth_profile(
            profile_name=profile_name,
            auth_type=auth_type,
            login_url=login_url,
            username_env=username_env,
            password_env=password_env,
            storage_state_path=storage_state_path,
            cookie_json_path=cookie_json_path,
            user_data_dir=user_data_dir,
        )
    except Exception as e:
        return {"error": str(e)}


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
        dict with profile_name, status ("captured" | "timeout"), storage_state_path, note
    """
    try:
        return await capture_auth.capture_auth_session(
            profile_name=profile_name,
            login_url=login_url,
            success_url_pattern=success_url_pattern,
            timeout_secs=timeout_secs,
            user_data_dir=user_data_dir,
        )
    except Exception as e:
        return {"error": str(e)}


@mcp.tool()
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
        return {"error": str(e)}


@mcp.tool()
async def evaluate_web_task(
    app_url: str,
    task: str,
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
        app_url: The website URL to evaluate
        task: Natural-language description of what to test (e.g. "Try the signup flow and note UX issues")
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
    try:
        return await evaluate_tools.evaluate_web_task(
            app_url=app_url,
            task=task,
            profile_name=profile_name,
            headless=headless,
            max_steps=max_steps,
            capture=capture,
            format=format,
            save_as_recorded_flow=save_as_recorded_flow,
            flow_name=flow_name,
        )
    except Exception as e:
        return {"error": str(e)}


@mcp.tool()
async def setup_browser_state(
    login_url: str,
    profile_name: str = "default",
    success_url_pattern: Optional[str] = None,
    timeout_secs: int = 120,
    user_data_dir: Optional[str] = None,
) -> dict:
    """Open an interactive browser for manual login, then save the session for reuse.

    Convenience alias for capture_auth_session. Opens a visible browser window at
    login_url — complete OAuth, MFA, or any login flow manually. The session state
    (cookies + localStorage) is saved automatically and reused by evaluate_web_task
    and other tools.

    Args:
        login_url: URL of the login page to open
        profile_name: Name to save the auth profile under (default: "default")
        success_url_pattern: URL substring that indicates login succeeded (e.g. "/dashboard")
        timeout_secs: Max seconds to wait for login (default: 120)
        user_data_dir: Optional persistent Chromium profile dir (for anti-bot OAuth)

    Returns:
        dict with profile_name, status ("captured" | "timeout"), storage_state_path, note
    """
    try:
        return await capture_auth.capture_auth_session(
            profile_name=profile_name,
            login_url=login_url,
            success_url_pattern=success_url_pattern,
            timeout_secs=timeout_secs,
            user_data_dir=user_data_dir,
        )
    except Exception as e:
        return {"error": str(e)}


@mcp.tool()
async def cancel_run(run_id: str) -> dict:
    """Cancel a running test and mark it as cancelled.

    Args:
        run_id: The run_id to cancel

    Returns:
        dict with run_id, previous_status, new_status
    """
    try:
        run = await sqlite.get_run(run_id)
        if not run:
            return {"error": f"Run {run_id} not found"}
        prev_status = run.get("status", "unknown")
        if prev_status in ("completed", "failed", "cancelled"):
            return {"run_id": run_id, "previous_status": prev_status, "note": "Run already terminated"}
        await sqlite.update_run_status(run_id, "cancelled")
        return {"run_id": run_id, "previous_status": prev_status, "new_status": "cancelled"}
    except Exception as e:
        return {"error": str(e)}


@mcp.tool()
async def record_test_flow(
    app_url: str,
    flow_name: str,
    goal: str,
    profile_name: Optional[str] = None,
    command: Optional[str] = None,
    business_criticality: Optional[str] = "other",
) -> dict:
    """Record a test flow by running a Browser-Use agent to accomplish a goal.

    Captures each action with selector, target_text, dom_fingerprint, per-step
    screenshots, and generates final assertion steps from a Gemini screenshot analysis.

    Args:
        app_url: The website URL to test
        flow_name: Short name for this flow (used as identifier)
        goal: Plain-English description of what to accomplish
        profile_name: Optional auth profile name (from save_auth_profile)
        command: Optional natural language command for additional context
        business_criticality: "revenue" | "activation" | "retention" | "support" | "other"

    Returns:
        dict with flow_id, flow_name, step_count, status, artifacts_dir
    """
    try:
        return await record.record_test_flow(
            app_url=app_url,
            flow_name=flow_name,
            goal=goal,
            profile_name=profile_name,
            command=command,
            business_criticality=business_criticality or "other",
        )
    except Exception as e:
        return {"error": str(e)}


@mcp.tool()
async def run_regression_test(
    app_url: str,
    flow_ids: list,
    profile_name: Optional[str] = None,
    headless: bool = True,
    run_mode: str = "hybrid",
    command: Optional[str] = None,
    auto_rerecord: bool = False,
) -> dict:
    """Run regression tests against recorded flows. Returns immediately; poll get_test_results for status.

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
    try:
        return await regression.run_regression_test(
            app_url=app_url,
            flow_ids=flow_ids,
            profile_name=profile_name,
            headless=headless,
            run_mode=run_mode,
            command=command,
            auto_rerecord=auto_rerecord,
        )
    except Exception as e:
        return {"error": str(e)}


@mcp.tool()
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
        return {"error": str(e)}


@mcp.tool()
async def get_test_results(run_id: str) -> dict:
    """Get structured results for a test run.

    Args:
        run_id: The run_id returned from run_regression_test

    Returns:
        dict with run_id, status, cases (with assertion_results, replay_mode_used,
        step_failure_index, artifact_paths), severity_counts, failed_cases, next_actions
    """
    try:
        return await results.get_test_results(run_id)
    except Exception as e:
        return {"error": str(e)}


@mcp.tool()
async def list_runs(limit: int = 20, status: Optional[str] = None) -> dict:
    """List recent regression runs, optionally filtered by status.

    Args:
        limit: Number of runs to return (default 20, max 200)
        status: Optional status filter ("queued", "running", "waiting_auth", "completed", "failed", "cancelled")
    """
    try:
        return await results.list_runs(limit=limit, status=status)
    except Exception as e:
        return {"error": str(e)}


@mcp.tool()
async def get_run_health_stream(run_id: str, limit: int = 500) -> dict:
    """Get control-plane health events for a run (queue/start/case/complete/fail)."""
    try:
        return await results.get_run_health_stream(run_id=run_id, limit=limit)
    except Exception as e:
        return {"error": str(e)}


@mcp.tool()
async def get_risk_analytics(limit_runs: int = 30) -> dict:
    """Aggregate flaky-step, transition-failure, and business-critical risk analytics."""
    try:
        return await results.get_risk_analytics(limit_runs=limit_runs)
    except Exception as e:
        return {"error": str(e)}


@mcp.tool()
async def list_recorded_tests() -> dict:
    """List all recorded test flows.

    Returns:
        dict with flows (list of {flow_id, flow_name, app_url, goal, created_at}), total
    """
    try:
        from blop.storage.sqlite import list_flows
        from blop.schemas import RecordedTestsResult
        flows = await list_flows()
        return RecordedTestsResult(flows=flows, total=len(flows)).model_dump()
    except Exception as e:
        return {"error": str(e)}


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
        return {"error": str(e)}


@mcp.tool()
async def validate_setup(
    app_url: Optional[str] = None,
    profile_name: Optional[str] = None,
) -> dict:
    """Check all preconditions before running tests.

    Verifies: GOOGLE_API_KEY, Chromium installation, SQLite DB access,
    optional app_url reachability, and optional auth profile validity.

    Args:
        app_url: Optional URL to check reachability
        profile_name: Optional auth profile name to validate

    Returns:
        dict with status ("ready" | "warnings" | "blocked"), checks, blockers, warnings
    """
    try:
        return await validate.validate_setup(
            app_url=app_url,
            profile_name=profile_name,
        )
    except Exception as e:
        return {"error": str(e)}


# ---------------------------------------------------------------------------
# Structured Assertion Tools — lightweight standalone verifications
# ---------------------------------------------------------------------------

@mcp.tool()
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
        return {"error": str(e)}


@mcp.tool()
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
        return {"error": str(e)}


@mcp.tool()
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
        return {"error": str(e)}


@mcp.tool()
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
        return {"error": str(e)}


@mcp.tool()
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
        return {"error": str(e)}


# ---------------------------------------------------------------------------
# Security Scanning Tools
# ---------------------------------------------------------------------------

@mcp.tool()
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
        return {"error": str(e)}


@mcp.tool()
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
        return {"error": str(e)}


# ---------------------------------------------------------------------------
# Network Mocking Tools
# ---------------------------------------------------------------------------

@mcp.tool()
async def mock_network_route(
    pattern: str,
    status: int = 200,
    body: Optional[str] = None,
    content_type: str = "application/json",
) -> dict:
    """Register a network route mock for use during regression test runs.

    Intercepted requests matching the pattern will receive the mocked response.
    Useful for testing error states, slow loading, and API contract changes.

    Args:
        pattern: URL pattern to intercept (glob or regex)
        status: HTTP status code to respond with (default: 200)
        body: Response body string
        content_type: Response content type (default: "application/json")
    """
    try:
        return await network_tools.mock_network_route(pattern, status, body, content_type)
    except Exception as e:
        return {"error": str(e)}


@mcp.tool()
async def clear_network_routes() -> dict:
    """Remove all registered network route mocks."""
    try:
        return await network_tools.clear_network_routes()
    except Exception as e:
        return {"error": str(e)}


# ---------------------------------------------------------------------------
# Code Generation Tool
# ---------------------------------------------------------------------------

@mcp.tool()
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
    try:
        from blop.engine.codegen import export_flow_as_code as _export
        return await _export(flow_id, language)
    except Exception as e:
        return {"error": str(e)}


# ---------------------------------------------------------------------------
# Storage State Management Tools
# ---------------------------------------------------------------------------

@mcp.tool()
async def get_browser_cookies(
    app_url: str,
    profile_name: Optional[str] = None,
) -> dict:
    """List all cookies for an app URL.

    Args:
        app_url: The URL to navigate to and read cookies from
        profile_name: Optional auth profile for authenticated pages
    """
    try:
        return await storage_tools.get_browser_cookies(app_url, profile_name)
    except Exception as e:
        return {"error": str(e)}


@mcp.tool()
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
    """Set a specific cookie in a browser context.

    Args:
        app_url: The URL context for the cookie
        name: Cookie name
        value: Cookie value
        domain: Cookie domain (defaults to URL hostname)
        path: Cookie path (default: "/")
        secure: Secure flag
        http_only: HttpOnly flag
        profile_name: Optional auth profile
    """
    try:
        return await storage_tools.set_browser_cookie(
            app_url, name, value, domain, path, secure, http_only, profile_name
        )
    except Exception as e:
        return {"error": str(e)}


@mcp.tool()
async def save_browser_state(
    app_url: str,
    profile_name: Optional[str] = None,
    filename: Optional[str] = None,
) -> dict:
    """Save the full browser storage state (cookies + localStorage) to a JSON file.

    Args:
        app_url: The URL to capture state from
        profile_name: Optional auth profile
        filename: Optional output file path (defaults to .blop/states/)
    """
    try:
        return await storage_tools.save_browser_state(app_url, profile_name, filename)
    except Exception as e:
        return {"error": str(e)}


# ---------------------------------------------------------------------------
# MCP v2 Tools — change intelligence + reliability control plane
# ---------------------------------------------------------------------------

@mcp.tool()
async def blop_v2_get_surface_contract() -> dict:
    """Get v2 MCP tool schemas and request examples."""
    try:
        return await v2_surface.get_surface_contract()
    except Exception as e:
        return {"error": str(e)}


@mcp.tool()
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
    try:
        return await v2_surface.capture_context(
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
    except Exception as e:
        return {"error": str(e)}


@mcp.tool()
async def blop_v2_compare_context(
    app_url: str,
    baseline_graph_id: str,
    candidate_graph_id: str,
    impact_lens: Optional[list[str]] = None,
) -> dict:
    """Compare two context graph versions and return impact summary."""
    try:
        return await v2_surface.compare_context(
            app_url=app_url,
            baseline_graph_id=baseline_graph_id,
            candidate_graph_id=candidate_graph_id,
            impact_lens=impact_lens,
        )
    except Exception as e:
        return {"error": str(e)}


@mcp.tool()
async def blop_v2_assess_release_risk(
    app_url: str,
    release_id: Optional[str] = None,
    baseline_ref: Optional[dict] = None,
    candidate_ref: Optional[dict] = None,
    criticality_weights: Optional[dict] = None,
) -> dict:
    """Assess release risk from context diff + run outcomes."""
    try:
        return await v2_surface.assess_release_risk(
            app_url=app_url,
            release_id=release_id,
            baseline_ref=baseline_ref,
            candidate_ref=candidate_ref,
            criticality_weights=criticality_weights,
        )
    except Exception as e:
        return {"error": str(e)}


@mcp.tool()
async def blop_v2_get_journey_health(
    app_url: str,
    window: str = "7d",
    journey_filter: Optional[list[str]] = None,
    criticality_filter: Optional[list[str]] = None,
) -> dict:
    """Get SLO-like health for key journeys across a time window."""
    try:
        return await v2_surface.get_journey_health(
            app_url=app_url,
            window=window,
            journey_filter=journey_filter,
            criticality_filter=criticality_filter,
        )
    except Exception as e:
        return {"error": str(e)}


@mcp.tool()
async def blop_v2_cluster_incidents(
    app_url: str,
    run_ids: Optional[list[str]] = None,
    window: str = "7d",
    min_cluster_size: int = 2,
) -> dict:
    """Cluster failures into deduplicated incidents with blast radius."""
    try:
        return await v2_surface.cluster_incidents(
            app_url=app_url,
            run_ids=run_ids,
            window=window,
            min_cluster_size=min_cluster_size,
        )
    except Exception as e:
        return {"error": str(e)}


@mcp.tool()
async def blop_v2_generate_remediation(
    cluster_id: str,
    format: str = "markdown",
    include_owner_hints: bool = True,
    include_fix_hypotheses: bool = True,
) -> dict:
    """Generate an action-ready remediation draft for an incident cluster."""
    try:
        return await v2_surface.generate_remediation(
            cluster_id=cluster_id,
            format=format,
            include_owner_hints=include_owner_hints,
            include_fix_hypotheses=include_fix_hypotheses,
        )
    except Exception as e:
        return {"error": str(e)}


@mcp.tool()
async def blop_v2_ingest_telemetry_signals(
    app_url: str,
    signals: list[dict],
    source: str = "custom",
) -> dict:
    """Ingest external telemetry for correlation against incidents."""
    try:
        return await v2_surface.ingest_telemetry_signals(
            app_url=app_url,
            signals=signals,
            source=source,
        )
    except Exception as e:
        return {"error": str(e)}


@mcp.tool()
async def blop_v2_get_correlation_report(
    app_url: str,
    window: str = "7d",
    min_confidence: float = 0.6,
) -> dict:
    """Correlate incident clusters with telemetry signals."""
    try:
        return await v2_surface.get_correlation_report(
            app_url=app_url,
            window=window,
            min_confidence=min_confidence,
        )
    except Exception as e:
        return {"error": str(e)}


@mcp.tool()
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
    try:
        return await v2_surface.suggest_flows_for_diff(
            app_url=app_url,
            changed_files=changed_files,
            changed_routes=changed_routes,
            limit=limit,
        )
    except Exception as e:
        return {"error": str(e)}


@mcp.tool()
async def blop_v2_autogenerate_flows(
    app_url: str,
    profile_name: Optional[str] = None,
    criticality_filter: Optional[list[str]] = None,
    record: bool = False,
    limit: int = 5,
) -> dict:
    """Auto-generate test flow specs from context graph intents that lack recorded flows.

    Finds intent nodes in the context graph that don't have a matching recorded flow,
    synthesizes flow specs from the intent metadata, and optionally records them.

    Args:
        app_url: The app URL (must have an existing context graph)
        profile_name: Optional auth profile for recording
        criticality_filter: Optional list of criticality levels to include (e.g. ["revenue", "activation"])
        record: If True, call record_test_flow for each synthesized flow
        limit: Maximum number of flows to synthesize (default 5)

    Returns:
        dict with app_url, synthesized[], recorded_flow_ids[], total_unmatched_intents
    """
    try:
        return await v2_surface.autogenerate_flows(
            app_url=app_url,
            profile_name=profile_name,
            criticality_filter=criticality_filter,
            record=record,
            limit=limit,
        )
    except Exception as e:
        return {"error": str(e)}


@mcp.tool()
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
        return {"error": str(e)}


# ---------------------------------------------------------------------------
# MCP Resources — read-only context for low-token agent planning
# ---------------------------------------------------------------------------

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


@mcp.resource("blop://flow/{flow_id}/stability-profile")
async def flow_stability_profile_resource(flow_id: str) -> dict:
    """Read-only stability profile for a recorded flow."""
    return await results.get_flow_stability_profile_resource(flow_id)


@mcp.resource("blop://prompts/list")
async def prompts_list_resource() -> dict:
    """List all available prompt templates with previews."""
    from blop.prompts import list_available_prompts
    return list_available_prompts()


@mcp.resource("blop://prompts/{name}")
async def prompt_resource(name: str) -> dict:
    """Read a specific prompt template by name (discover, repair, remediation, next_actions)."""
    from blop.prompts import get_prompt, DISCOVER_PROMPT, REPAIR_STEP_PROMPT, REMEDIATION_PROMPT, NEXT_ACTIONS_PROMPT
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
    return """First run validate_setup to confirm your environment is ready:
  validate_setup(app_url="https://your-app.com")

Then map interface structure before planning tests:
  explore_site_inventory(
    app_url="https://your-app.com",
    max_depth=2,
    max_pages=20
  )

If you need a focused snapshot for one route, capture compact ARIA structure:
  get_page_structure(
    app_url="https://your-app.com",
    url="https://your-app.com/pricing"
  )

After structure mapping, discover the most important test flows:
  discover_test_flows(
    app_url="https://your-app.com",
    business_goal="Find the 5 most revenue-critical flows including signup, onboarding, and billing."
  )

The response will include flows with a business_criticality field (revenue, activation, retention, support, other).
Start by recording flows tagged "revenue" or "activation" — those are the ones that will hurt most if broken."""


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
    return """To run a quick smoke regression against all recorded flows:

1. List available flows:
   list_recorded_tests()

2. Run regression (returns immediately — poll for results):
   run_regression_test(
     app_url="https://your-app.com",
     flow_ids=["<flow_id_1>", "<flow_id_2>"],
     profile_name="staging"  # optional
   )
   The status will be "queued" → "running" → "completed"

3. Poll for results (repeat until status is "completed" or "failed"):
   get_test_results(run_id="<run_id>")

The report includes severity_counts with revenue/activation flows labeled as
"BLOCKER in revenue flow: checkout" so you can triage at a glance."""


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

1) Run regression:
   run_regression_test(app_url="https://your-app.com", flow_ids=["..."], run_mode="hybrid")

2) Poll:
   get_test_results(run_id="<run_id>")

3) Read artifacts and stability resources:
   - blop://run/<run_id>/artifact-index
   - blop://flow/<flow_id>/stability-profile

4) Use those resources to prioritize:
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
   setup_browser_state(
     login_url="https://your-app.com/login",
     profile_name="myapp",
     success_url_pattern="/dashboard"
   )

2. Evaluate the app with a natural-language task:
   evaluate_web_task(
     app_url="https://your-app.com",
     task="Try the full signup flow and report any UX issues",
     profile_name="myapp"  # optional
   )

   This returns a complete report with screenshots, console errors, network
   failures, and agent step timeline — no need to discover or record flows first.

3. If the evaluation looks good, promote it to a regression test:
   evaluate_web_task(
     app_url="https://your-app.com",
     task="Complete checkout with a test product",
     save_as_recorded_flow=True,
     flow_name="checkout_flow"
   )

   The recorded flow can then be replayed with run_regression_test."""


def run() -> int:
    """Entry point for the MCP server."""
    import asyncio
    asyncio.run(init_db())
    try:
        mcp.run()
        return 0
    except Exception:
        return 1


if __name__ == "__main__":
    run()
