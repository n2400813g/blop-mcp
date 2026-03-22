"""validate_setup tool — checks all preconditions before the user runs anything."""
from __future__ import annotations

import os
from pathlib import Path
from typing import Optional

from blop.config import BLOP_DB_PATH, BLOP_DEBUG_LOG, runtime_config_issues, validate_app_url


async def validate_setup(
    app_url: Optional[str] = None,
    profile_name: Optional[str] = None,
) -> dict:
    checks: list[dict] = []
    blockers: list[str] = []
    warnings: list[str] = []

    # 0. Runtime config sanity (types/ranges/path policy)
    cfg_errors, cfg_warnings = runtime_config_issues()
    checks.append(
        {
            "name": "runtime_config",
            "passed": not cfg_errors,
            "message": "Runtime config validated" if not cfg_errors else "; ".join(cfg_errors[:3]),
        }
    )
    blockers.extend(cfg_errors)
    warnings.extend(cfg_warnings)

    def _check_writable(path: Path, *, as_file: bool = False) -> str | None:
        try:
            target_dir = path.parent if as_file else path
            target_dir.mkdir(parents=True, exist_ok=True)
            probe = target_dir / ".blop_validate_write_test"
            probe.touch()
            probe.unlink(missing_ok=True)
            return None
        except Exception as exc:
            return str(exc)

    # 1. LLM API key (provider-aware)
    provider = os.getenv("BLOP_LLM_PROVIDER", "google").lower()
    if provider == "anthropic":
        api_key = os.getenv("ANTHROPIC_API_KEY", "")
        key_name = "ANTHROPIC_API_KEY"
        key_hint = "Anthropic Claude agent and flow planning"
    elif provider == "openai":
        api_key = os.getenv("OPENAI_API_KEY", "")
        key_name = "OPENAI_API_KEY"
        key_hint = "OpenAI agent and flow planning"
    else:
        api_key = os.getenv("GOOGLE_API_KEY", "")
        key_name = "GOOGLE_API_KEY"
        key_hint = "Gemini agent and flow planning"
    if api_key:
        checks.append({"name": key_name, "passed": True, "message": f"Set and non-empty (provider: {provider})"})
    else:
        checks.append({"name": key_name, "passed": False, "message": f"Not set — required for {key_hint}"})
        blockers.append(f"{key_name} environment variable is not set")

    # 2. Chromium installed
    chromium_ok = False
    try:
        from playwright.async_api import async_playwright
        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True)
            await browser.close()
        chromium_ok = True
    except Exception as exc:
        chromium_ok = False
        err = str(exc)

    if chromium_ok:
        checks.append({"name": "chromium_installed", "passed": True, "message": "Chromium launches successfully"})
    else:
        checks.append({"name": "chromium_installed", "passed": False, "message": "Chromium not found — run: playwright install chromium"})
        blockers.append("Chromium not installed. Run: playwright install chromium")

    # 3. SQLite DB accessible
    db_ok = False
    try:
        from blop.storage.sqlite import init_db
        await init_db()
        db_ok = True
    except Exception as exc:
        db_ok = False
        db_err = str(exc)

    if db_ok:
        checks.append({"name": "sqlite_db", "passed": True, "message": "Database initialized and accessible"})
    else:
        checks.append({"name": "sqlite_db", "passed": False, "message": f"DB init failed: {db_err}"})
        blockers.append(f"SQLite database initialization failed: {db_err}")

    from blop.storage import files as file_store

    db_path_error = _check_writable(Path(BLOP_DB_PATH), as_file=True)
    checks.append(
        {
            "name": "db_path_writable",
            "passed": db_path_error is None,
            "message": "DB path parent directory is writable"
            if db_path_error is None
            else f"DB path not writable: {db_path_error}",
        }
    )
    if db_path_error:
        blockers.append(f"BLOP_DB_PATH parent directory is not writable: {db_path_error}")

    runs_dir = file_store._runs_dir()
    runs_error = _check_writable(runs_dir)
    checks.append(
        {
            "name": "runs_dir_writable",
            "passed": runs_error is None,
            "message": f"Runs directory is writable: {runs_dir}"
            if runs_error is None
            else f"Runs directory not writable ({runs_dir}): {runs_error}",
        }
    )
    if runs_error:
        blockers.append(f"BLOP_RUNS_DIR is not writable: {runs_error}")

    if BLOP_DEBUG_LOG:
        log_error = _check_writable(Path(BLOP_DEBUG_LOG), as_file=True)
        checks.append(
            {
                "name": "debug_log_writable",
                "passed": log_error is None,
                "message": "Debug log path parent directory is writable"
                if log_error is None
                else f"Debug log path not writable: {log_error}",
            }
        )
        if log_error:
            warnings.append(f"BLOP_DEBUG_LOG parent directory is not writable: {log_error}")

    # 4. app_url reachable (optional)
    if app_url:
        url_err = validate_app_url(app_url)
        if url_err:
            checks.append({"name": "app_url_reachable", "passed": False, "message": url_err})
            warnings.append(url_err)
        else:
            try:
                import urllib.request
                req = urllib.request.Request(app_url, headers={"User-Agent": "blop-validate/1.0"})
                with urllib.request.urlopen(req, timeout=10) as resp:
                    checks.append({"name": "app_url_reachable", "passed": True, "message": f"{app_url} responded with HTTP {resp.status}"})
            except Exception as exc:
                checks.append({"name": "app_url_reachable", "passed": False, "message": f"{app_url} not reachable: {exc}"})
                warnings.append(f"app_url {app_url!r} is not reachable. Ensure the app is running.")

    # 5. Auth profile valid (optional)
    if profile_name:
        try:
            from blop.storage.sqlite import get_auth_profile
            from blop.engine.auth import resolve_storage_state_for_profile, validate_auth_session
            profile = await get_auth_profile(profile_name)
            if profile is None:
                checks.append({"name": "auth_profile", "passed": False, "message": f"Profile '{profile_name}' not found — run save_auth_profile first"})
                warnings.append(f"Auth profile '{profile_name}' not found")
            else:
                state = await resolve_storage_state_for_profile(
                    profile_name,
                    allow_auto_env=False,
                    profile=profile,
                )
                if not state:
                    checks.append({"name": "auth_profile", "passed": False, "message": f"Profile '{profile_name}' could not resolve storage state"})
                    warnings.append(f"Auth profile '{profile_name}' exists but storage state could not be resolved")
                elif profile.auth_type == "storage_state" and app_url:
                    # Validate the session is still live, not just that the file exists
                    session_valid = await validate_auth_session(state, app_url)
                    if session_valid:
                        checks.append({"name": "auth_profile", "passed": True, "message": f"Profile '{profile_name}' resolved and session is active"})
                    else:
                        checks.append({"name": "auth_profile", "passed": False, "message": f"Profile '{profile_name}' storage state exists but session has expired — re-run capture_auth_session"})
                        warnings.append(f"Auth session for '{profile_name}' has expired")
                else:
                    checks.append({"name": "auth_profile", "passed": True, "message": f"Profile '{profile_name}' resolved successfully"})
        except Exception as exc:
            checks.append({"name": "auth_profile", "passed": False, "message": f"Auth profile check failed: {exc}"})
            warnings.append(f"Auth profile validation error: {exc}")

    if blockers:
        status = "blocked"
    elif warnings:
        status = "warnings"
    else:
        status = "ready"

    suggested_next_steps: list[str] = []
    if blockers:
        for blocker in blockers:
            if "GOOGLE_API_KEY" in blocker or "ANTHROPIC_API_KEY" in blocker or "OPENAI_API_KEY" in blocker:
                key_name = blocker.split()[0]
                suggested_next_steps.append(f"Set {key_name}: export {key_name}=your_api_key_here")
            elif "Chromium" in blocker:
                suggested_next_steps.append("Install Chromium: playwright install chromium")
            elif "SQLite" in blocker or "database" in blocker.lower():
                suggested_next_steps.append("Fix DB: ensure .blop/ directory is writable (mkdir -p .blop && chmod 755 .blop)")
            else:
                suggested_next_steps.append(f"Fix blocker: {blocker}")
    elif status == "ready":
        auth_warning = any("auth" in w.lower() or "session" in w.lower() for w in warnings)
        if auth_warning or (profile_name and any(not c.get("passed") for c in checks if c["name"] == "auth_profile")):
            suggested_next_steps.append(
                "Re-capture auth session: capture_auth_session(login_url='https://your-app.com/login', profile_name='your_profile')"
            )
        if not app_url:
            suggested_next_steps.append(
                "Verify your app is reachable: validate_setup(app_url='https://your-app.com')"
            )
        else:
            if not profile_name:
                suggested_next_steps.append(
                    "If your app requires login: capture_auth_session(login_url='https://your-app.com/login', profile_name='default')"
                )
            suggested_next_steps.append(
                f"Discover test flows: discover_test_flows(app_url='{app_url}')"
            )
            suggested_next_steps.append(
                "Record a flow: record_test_flow(app_url='...', flow_name='...', goal='...')"
            )
            suggested_next_steps.append(
                "Run regression: run_regression_test(app_url='...', flow_ids=['...'])"
            )
            suggested_next_steps.append(
                "Get results: get_test_results(run_id='...')"
            )
    elif status == "warnings":
        auth_warning = any("auth" in w.lower() or "session" in w.lower() for w in warnings)
        if auth_warning:
            suggested_next_steps.append(
                "Fix auth: capture_auth_session(login_url='https://your-app.com/login', profile_name='your_profile')"
            )
        for w in warnings:
            if "not reachable" in w:
                suggested_next_steps.append("Ensure your app is running and accessible from this machine.")

    return {
        "status": status,
        "checks": checks,
        "blockers": blockers,
        "warnings": warnings,
        "suggested_next_steps": suggested_next_steps,
    }


async def validate_release_setup(
    app_url: Optional[str] = None,
    profile_name: Optional[str] = None,
) -> dict:
    """Canonical MVP alias for validate_setup.

    Checks all preconditions (API key, Chromium, DB, app reachability, auth)
    before running a release check.
    """
    result = await validate_setup(app_url=app_url, profile_name=profile_name)
    # Adjust suggested_next_steps to reference canonical tool names and arguments.
    steps = result.get("suggested_next_steps", [])
    updated_steps = []
    for step in steps:
        step = step.replace("discover_test_flows", "discover_critical_journeys")
        step = step.replace(
            "Run regression: run_regression_test(app_url='...', flow_ids=['...'])",
            "Run release check: run_release_check(app_url='...', flow_ids=['...'], mode='replay')",
        )
        step = step.replace("run_regression_test", "run_release_check")
        step = step.replace("flow_ids=['...']", "flow_ids=['...']")
        updated_steps.append(step)
    result["suggested_next_steps"] = updated_steps
    return result
