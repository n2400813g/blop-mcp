"""validate_setup tool — checks all preconditions before the user runs anything."""

from __future__ import annotations

import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from blop.config import (
    BLOP_API_TOKEN,
    BLOP_DB_PATH,
    BLOP_DEBUG_LOG,
    BLOP_HOSTED_URL,
    BLOP_PROJECT_ID,
    hosted_sync_config_snapshot,
    runtime_config_issues,
    runtime_posture_snapshot,
    validate_app_url,
)
from blop.schemas import TelemetrySignal
from blop.stability import build_validation_stability_readiness, classify_validation_issue


def _build_validation_summary(result: dict) -> dict:
    checks = result.get("checks", [])
    blockers = result.get("blockers", [])
    warnings = result.get("warnings", [])
    return {
        "passed": sum(1 for check in checks if check.get("passed")),
        "warning_count": len(warnings),
        "blocked_count": len(blockers),
        "total_checks": len(checks),
    }


def _build_validation_headline(result: dict, app_url: str | None, profile_name: str | None) -> str:
    status = result.get("status", "warnings")
    warnings = result.get("warnings", [])
    if status == "blocked":
        return "Release setup is blocked. Fix the blocking checks before running release-critical flows."
    if any("expired" in warning.lower() for warning in warnings):
        return "Release setup is partially ready, but the auth session has expired and must be refreshed."
    if profile_name and any("could not be resolved" in warning.lower() for warning in warnings):
        return "Release setup is partially ready, but the selected auth profile is not usable yet."
    if app_url:
        return "Release setup looks ready enough to continue with journey discovery and replay."
    return "Environment checks passed. Validate a specific app_url next to confirm release readiness."


def _build_validation_recommended_action(result: dict) -> str:
    steps = result.get("suggested_next_steps", []) or []
    if steps:
        return steps[0]
    status = result.get("status", "warnings")
    warnings = result.get("warnings", []) or []
    if status == "blocked":
        return "Resolve the highest-priority blocker and run validate_release_setup again."
    if any("expired" in warning.lower() for warning in warnings):
        return "Refresh the expired auth session with capture_auth_session(...), then re-run validate_release_setup."
    if any("could not be resolved" in warning.lower() for warning in warnings):
        return "Repair or re-capture the auth profile before attempting replay."
    if status == "warnings":
        return "Resolve the top warning and re-check setup before running a release check."
    return "Discover release-critical journeys and prepare replay coverage."


def _augment_validate_result(result: dict, *, app_url: str | None, profile_name: str | None) -> dict:
    result["check_summary"] = _build_validation_summary(result)
    result["headline"] = _build_validation_headline(result, app_url, profile_name)
    result["recommended_action"] = _build_validation_recommended_action(result)
    result["stability_readiness"] = build_validation_stability_readiness(result)
    return result


def _bucket_validation_issues(result: dict) -> dict:
    bucketed_blockers: list[dict] = []
    bucketed_warnings: list[dict] = []
    checks = {check.get("name"): check for check in result.get("checks", [])}
    for issue in result.get("blockers", []) or []:
        matched_check = _match_issue_to_check(issue, checks)
        classified = classify_validation_issue(
            matched_check.get("name", "runtime_validation"),
            matched_check.get("message", issue),
            passed=False,
        )
        bucketed_blockers.append(
            {
                "message": issue,
                "check_name": matched_check.get("name"),
                **classified,
            }
        )
    for issue in result.get("warnings", []) or []:
        matched_check = _match_issue_to_check(issue, checks)
        classified = classify_validation_issue(
            matched_check.get("name", "runtime_validation"),
            matched_check.get("message", issue),
            passed=False,
        )
        bucketed_warnings.append(
            {
                "message": issue,
                "check_name": matched_check.get("name"),
                **classified,
            }
        )
    result["bucketed_blockers"] = bucketed_blockers
    result["bucketed_warnings"] = bucketed_warnings
    return result


def _match_issue_to_check(issue: str, checks: dict) -> dict:
    lowered = (issue or "").lower()
    for check in checks.values():
        message = str(check.get("message", "")).lower()
        name = str(check.get("name", "")).lower()
        if name and name in lowered:
            return check
        if message and (message in lowered or lowered in message):
            return check
    if "auth" in lowered or "profile" in lowered:
        return checks.get("auth_profile", {"name": "auth_profile", "message": issue})
    if "chromium" in lowered:
        return checks.get("chromium_installed", {"name": "chromium_installed", "message": issue})
    if "reachable" in lowered or "running" in lowered:
        return checks.get("app_url_reachable", {"name": "app_url_reachable", "message": issue})
    return {"name": "runtime_validation", "message": issue}


async def _record_validation_observations(result: dict, app_url: str | None) -> None:
    if not app_url:
        return
    observations = list(result.get("bucketed_blockers", []) or []) + list(result.get("bucketed_warnings", []) or [])
    if not observations:
        return
    signals: list[TelemetrySignal] = []
    ts = datetime.now(timezone.utc).isoformat()
    for issue in observations:
        bucket = issue.get("stability_bucket")
        if not bucket:
            continue
        signals.append(
            TelemetrySignal(
                app_url=app_url,
                source="custom",
                ts=ts,
                signal_type="custom",
                value=1.0,
                unit="count",
                tags={
                    "surface": "validate",
                    "bucket": bucket,
                    "status": "fail" if issue in (result.get("bucketed_blockers", []) or []) else "warning",
                    "reason_code": str(issue.get("check_name") or "runtime_validation"),
                },
            )
        )
    if signals:
        from blop.storage import sqlite

        try:
            await sqlite.save_telemetry_signals(signals)
        except Exception:
            pass


async def validate_setup(
    app_url: Optional[str] = None,
    profile_name: Optional[str] = None,
    check_mobile: bool = False,
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
    except Exception:
        chromium_ok = False

    if chromium_ok:
        checks.append({"name": "chromium_installed", "passed": True, "message": "Chromium launches successfully"})
    else:
        checks.append(
            {
                "name": "chromium_installed",
                "passed": False,
                "message": "Chromium not found — run: playwright install chromium",
            }
        )
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
                    checks.append(
                        {
                            "name": "app_url_reachable",
                            "passed": True,
                            "message": f"{app_url} responded with HTTP {resp.status}",
                        }
                    )
            except Exception as exc:
                checks.append(
                    {"name": "app_url_reachable", "passed": False, "message": f"{app_url} not reachable: {exc}"}
                )
                warnings.append(f"app_url {app_url!r} is not reachable. Ensure the app is running.")

    # 5. Auth profile valid (optional)
    if profile_name:
        try:
            from blop.engine.auth import resolve_storage_state_for_profile, validate_auth_session
            from blop.storage.sqlite import get_auth_profile

            profile = await get_auth_profile(profile_name)
            if profile is None:
                checks.append(
                    {
                        "name": "auth_profile",
                        "passed": False,
                        "message": f"Profile '{profile_name}' not found — run save_auth_profile first",
                    }
                )
                warnings.append(f"Auth profile '{profile_name}' not found")
            else:
                state = await resolve_storage_state_for_profile(
                    profile_name,
                    allow_auto_env=False,
                    profile=profile,
                )
                if not state:
                    checks.append(
                        {
                            "name": "auth_profile",
                            "passed": False,
                            "message": f"Profile '{profile_name}' could not resolve storage state",
                        }
                    )
                    warnings.append(f"Auth profile '{profile_name}' exists but storage state could not be resolved")
                elif profile.auth_type == "storage_state" and app_url:
                    # Validate the session is still live, not just that the file exists
                    session_valid = await validate_auth_session(state, app_url)
                    if session_valid:
                        checks.append(
                            {
                                "name": "auth_profile",
                                "passed": True,
                                "message": f"Profile '{profile_name}' resolved and session is active",
                            }
                        )
                    else:
                        checks.append(
                            {
                                "name": "auth_profile",
                                "passed": False,
                                "message": f"Profile '{profile_name}' storage state exists but session has expired — re-run capture_auth_session",
                            }
                        )
                        warnings.append(f"Auth session for '{profile_name}' has expired")
                else:
                    checks.append(
                        {
                            "name": "auth_profile",
                            "passed": True,
                            "message": f"Profile '{profile_name}' resolved successfully",
                        }
                    )
        except Exception as exc:
            checks.append({"name": "auth_profile", "passed": False, "message": f"Auth profile check failed: {exc}"})
            warnings.append(f"Auth profile validation error: {exc}")

    # 6. Hosted sync connectivity (optional, non-blocking)
    hosted_sync = hosted_sync_config_snapshot()
    if hosted_sync["partial"]:
        missing = ", ".join(hosted_sync["missing_fields"])
        checks.append(
            {
                "name": "hosted_sync_config",
                "passed": False,
                "message": f"Hosted sync partially configured; missing {missing}",
            }
        )
        warnings.append(
            "Hosted sync is partially configured. Set BLOP_HOSTED_URL, BLOP_API_TOKEN, "
            "and BLOP_PROJECT_ID together, or leave them all unset for local-only mode."
        )
    elif hosted_sync["enabled"]:
        try:
            from blop.sync.client import SyncClient

            probe = await SyncClient(BLOP_HOSTED_URL, BLOP_API_TOKEN).probe_connection(BLOP_PROJECT_ID)
            if probe:
                detail = (
                    f"Connected to workspace {probe.get('workspace_id')} "
                    f"(scope={probe.get('token_scope')}, project={probe.get('requested_project_id') or probe.get('token_project_id')})"
                )
                checks.append(
                    {
                        "name": "hosted_sync_connection",
                        "passed": True,
                        "message": detail,
                    }
                )
            else:
                checks.append(
                    {
                        "name": "hosted_sync_connection",
                        "passed": False,
                        "message": "Configured, but the hosted sync connection could not be verified",
                    }
                )
                warnings.append("Hosted sync is configured, but Blop Cloud connectivity or token validation failed.")
        except Exception as exc:
            checks.append(
                {
                    "name": "hosted_sync_connection",
                    "passed": False,
                    "message": f"Hosted sync verification failed: {exc}",
                }
            )
            warnings.append(f"Hosted sync verification error: {exc}")
    else:
        checks.append(
            {
                "name": "hosted_sync_config",
                "passed": True,
                "message": "Local-only mode (Blop Cloud sync not configured)",
            }
        )

    # 7. Appium server reachability (mobile, optional)
    if check_mobile:
        try:
            from blop.engine.mobile.driver import check_appium_reachable

            appium_ok, appium_msg = await check_appium_reachable()
            checks.append({"name": "appium_server", "passed": appium_ok, "message": appium_msg})
            if not appium_ok:
                warnings.append(
                    f"Appium server not reachable: {appium_msg}. Start it with: appium (requires npm install -g appium)"
                )
        except ImportError:
            checks.append(
                {
                    "name": "appium_server",
                    "passed": False,
                    "message": "Mobile extra not installed — run: pip install blop-mcp[mobile]",
                }
            )
            warnings.append("Mobile extra not installed. Run: pip install blop-mcp[mobile]")

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
                suggested_next_steps.append(
                    "Fix DB: ensure .blop/ directory is writable (mkdir -p .blop && chmod 755 .blop)"
                )
            else:
                suggested_next_steps.append(f"Fix blocker: {blocker}")
    elif status == "ready":
        auth_warning = any("auth" in w.lower() or "session" in w.lower() for w in warnings)
        if auth_warning or (profile_name and any(not c.get("passed") for c in checks if c["name"] == "auth_profile")):
            suggested_next_steps.append(
                "Re-capture auth session: capture_auth_session(login_url='https://your-app.com/login', profile_name='your_profile')"
            )
        if not app_url:
            suggested_next_steps.append("Verify your app is reachable: validate_setup(app_url='https://your-app.com')")
        else:
            if not profile_name:
                suggested_next_steps.append(
                    "If your app requires login: capture_auth_session(login_url='https://your-app.com/login', profile_name='default')"
                )
            suggested_next_steps.append(f"Discover test flows: discover_test_flows(app_url='{app_url}')")
            suggested_next_steps.append("Record a flow: record_test_flow(app_url='...', flow_name='...', goal='...')")
            suggested_next_steps.append("Run regression: run_regression_test(app_url='...', flow_ids=['...'])")
            suggested_next_steps.append("Get results: get_test_results(run_id='...')")
    # Mobile-specific guidance
    if check_mobile:
        appium_failed = any(not c.get("passed") for c in checks if c.get("name") == "appium_server")
        if appium_failed:
            if any("not installed" in w for w in warnings):
                suggested_next_steps.insert(0, "Install mobile extra: pip install blop-mcp[mobile]")
            else:
                suggested_next_steps.insert(0, "Start Appium: appium (then re-run validate_setup(check_mobile=True))")
                suggested_next_steps.insert(1, "See docs/mobile_setup.md for full mobile prerequisites")

    if status == "warnings":
        auth_warning = any("auth" in w.lower() or "session" in w.lower() for w in warnings)
        if auth_warning:
            suggested_next_steps.append(
                "Fix auth: capture_auth_session(login_url='https://your-app.com/login', profile_name='your_profile')"
            )
        if any("hosted sync" in w.lower() or "blop cloud" in w.lower() for w in warnings):
            suggested_next_steps.append(
                "Repair optional cloud sync: set BLOP_HOSTED_URL, BLOP_API_TOKEN, and BLOP_PROJECT_ID together, "
                "then run blop-wizard doctor --verbose."
            )
        for w in warnings:
            if "not reachable" in w:
                suggested_next_steps.append("Ensure your app is running and accessible from this machine.")

    result = {
        "status": status,
        "checks": checks,
        "blockers": blockers,
        "warnings": warnings,
        "suggested_next_steps": suggested_next_steps,
        "runtime_posture": runtime_posture_snapshot(),
    }
    result = _bucket_validation_issues(result)
    await _record_validation_observations(result, app_url)
    return _augment_validate_result(result, app_url=app_url, profile_name=profile_name)


async def validate_release_setup(
    app_url: Optional[str] = None,
    profile_name: Optional[str] = None,
    check_mobile: bool = False,
) -> dict:
    """Canonical MVP alias for validate_setup.

    Checks all preconditions (API key, Chromium, DB, app reachability, auth)
    before running a release check.

    Args:
        check_mobile: If True, also checks Appium server reachability for mobile testing.
    """
    result = await validate_setup(app_url=app_url, profile_name=profile_name, check_mobile=check_mobile)
    status = result.get("status", "warnings")

    canonical_steps: list[str] = []
    if status == "blocked":
        canonical_steps = list(result.get("suggested_next_steps", []))
    elif status == "warnings":
        if app_url:
            if profile_name:
                canonical_steps.append(
                    f"Refresh auth first: capture_auth_session(login_url='{app_url.rstrip('/')}/login', profile_name='{profile_name}')"
                )
                canonical_steps.append(
                    f"Re-run preflight after auth refresh: validate_release_setup(app_url='{app_url}', profile_name='{profile_name}')"
                )
            canonical_steps.append(
                f"Review release-critical journey coverage: discover_critical_journeys(app_url='{app_url}')"
            )
            canonical_steps.append("Inspect the recorded release-gating journey inventory: read blop://journeys")
        if profile_name:
            canonical_steps.append(
                "Re-check blocker evidence after auth issues are fixed: triage_release_blocker(release_id='...', run_id='...')"
            )
        else:
            canonical_steps.append(
                "If protected journeys gate this release, capture auth with capture_auth_session(...) before running the release check."
            )
    else:
        if app_url:
            canonical_steps.append(f"Discover critical journeys: discover_critical_journeys(app_url='{app_url}')")
            canonical_steps.append("Review recorded release-gating journeys: read blop://journeys")
            canonical_steps.append(
                "If a release-gating journey is missing, record it with record_test_flow(app_url='...', flow_name='...', goal='...', business_criticality='revenue')"
            )
            canonical_steps.append(
                f"Run the release-confidence check: run_release_check(app_url='{app_url}', flow_ids=['...'], mode='replay')"
            )
            canonical_steps.append(
                f"Optional advisory preflight before replay: run_release_check(app_url='{app_url}', flow_ids=['...'], mode='replay', smoke_preflight=True)"
            )
            canonical_steps.append(
                "When the run completes, inspect the brief and triage blockers: read blop://release/{release_id}/brief, then call triage_release_blocker(release_id='...') if needed"
            )
        else:
            canonical_steps.append(
                "Validate a specific release target next: validate_release_setup(app_url='https://your-app.com', profile_name='your_profile')"
            )

    result["suggested_next_steps"] = canonical_steps or list(result.get("suggested_next_steps", []))
    return _augment_validate_result(result, app_url=app_url, profile_name=profile_name)
