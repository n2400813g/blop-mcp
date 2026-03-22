from __future__ import annotations

import os
from datetime import datetime, timezone
from urllib.parse import quote

from blop.engine.context_graph import get_context_graph_summary, get_next_checks_for_release_scope
from blop.reporting import results as reporting
from blop.schemas import FailureCase
from blop.storage import sqlite


async def _build_auth_provenance(run: dict, events: list[dict]) -> dict:
    profile_name = run.get("profile_name")
    provenance = {
        "profile_name": profile_name,
        "auth_used": bool(profile_name),
        "auth_source": None,
        "storage_state_path": None,
        "user_data_dir": None,
        "session_validation_status": "unknown_not_captured",
        "landed_authenticated": None,
        "landing_url": None,
        "expected_url": None,
        "landing_page_title": None,
    }
    if not profile_name:
        for event in events:
            if event.get("event_type") == "auth_landing_observed":
                payload = event.get("payload", {}) or {}
                provenance["landed_authenticated"] = payload.get("landed_authenticated")
                provenance["landing_url"] = payload.get("landed_url")
                provenance["expected_url"] = payload.get("expected_url")
                provenance["landing_page_title"] = payload.get("page_title")
                break
        return provenance

    def _apply_event_payloads(target: dict) -> dict:
        for event in events:
            payload = event.get("payload", {}) or {}
            if event.get("event_type") == "auth_context_resolved":
                target["auth_used"] = payload.get("auth_used", target["auth_used"])
                target["auth_source"] = payload.get("auth_source", target["auth_source"])
                target["storage_state_path"] = payload.get("storage_state_path", target["storage_state_path"])
                target["user_data_dir"] = payload.get("user_data_dir", target["user_data_dir"])
                target["session_validation_status"] = payload.get(
                    "session_validation_status",
                    target["session_validation_status"],
                )
            elif event.get("event_type") == "auth_landing_observed" and target["landing_url"] is None:
                target["landed_authenticated"] = payload.get("landed_authenticated")
                target["landing_url"] = payload.get("landed_url")
                target["expected_url"] = payload.get("expected_url")
                target["landing_page_title"] = payload.get("page_title")
        return target

    try:
        profile = await sqlite.get_auth_profile(profile_name)
    except Exception:
        profile = None

    if not profile:
        provenance["auth_source"] = "missing_profile"
        return _apply_event_payloads(provenance)

    provenance["auth_source"] = profile.auth_type
    provenance["storage_state_path"] = profile.storage_state_path
    provenance["user_data_dir"] = profile.user_data_dir
    if profile.auth_type == "storage_state" and profile.storage_state_path:
        provenance["session_validation_status"] = "unvalidated_storage_state"
    elif profile.auth_type == "cookie_json" and profile.cookie_json_path:
        provenance["session_validation_status"] = "unvalidated_cookie_json"
    elif profile.auth_type == "env_login":
        provenance["session_validation_status"] = "env_login_profile"

    return _apply_event_payloads(provenance)


def _waiting_auth_message(auth_provenance: dict, profile_name: str | None) -> str:
    session_status = auth_provenance.get("session_validation_status")
    name = profile_name or auth_provenance.get("profile_name") or "the selected profile"
    messages = {
        "missing_profile": f"Run is waiting for auth because profile '{name}' was not found. Save or capture that profile, then retry.",
        "unresolved_storage_state": f"Run is waiting for auth because profile '{name}' could not resolve a usable session. Refresh the profile and retry.",
        "expired_session": f"Run is waiting for auth because profile '{name}' has an expired session. Re-run capture_auth_session or refresh the storage state, then retry.",
        "redirected_to_auth": f"Run is waiting for auth because profile '{name}' redirected back to login during validation. Refresh the session and retry.",
        "validation_error": f"Run is waiting for auth because session validation for profile '{name}' failed unexpectedly. Re-validate the profile and retry.",
    }
    return messages.get(
        session_status,
        "Run is waiting for auth. Refresh or capture a valid session, validate it against the target app, then retry.",
    )


def _annotate_staleness(rec: dict, completed_at: str | None, run_status: str) -> dict:
    """Add stale=True/False to a release_recommendation dict."""
    if run_status not in ("completed", "failed"):
        rec["stale"] = False
        return rec
    stale_hours = int(os.getenv("BLOP_RECOMMENDATION_STALE_HOURS", "24"))
    stale = False
    if completed_at:
        try:
            completed_dt = datetime.fromisoformat(completed_at.replace("Z", "+00:00"))
            if completed_dt.tzinfo is None:
                completed_dt = completed_dt.replace(tzinfo=timezone.utc)
            age_hours = (datetime.now(timezone.utc) - completed_dt).total_seconds() / 3600
            stale = age_hours > stale_hours
        except Exception:
            pass
    rec["stale"] = stale
    if stale:
        rec["stale_reason"] = (
            f"Run completed more than {stale_hours}h ago. "
            "Re-run for a fresh recommendation."
        )
    return rec


async def get_test_results(run_id: str) -> dict:
    run = await sqlite.get_run(run_id)
    if not run:
        return {"error": f"Run {run_id} not found"}

    # Try run_cases table first, fall back to cases_json in runs
    cases = await sqlite.list_cases_for_run(run_id)
    if not cases and run.get("cases"):
        cases = [FailureCase(**c) for c in run["cases"]]

    report = await reporting.build_report(run, cases)

    # Annotate recommendation with staleness
    rec = report.get("release_recommendation", {})
    report["release_recommendation"] = _annotate_staleness(
        rec, run.get("completed_at"), run.get("status", "unknown")
    )
    events = await sqlite.list_run_health_events(run_id, limit=500)
    report["auth_provenance"] = await _build_auth_provenance(run, events)
    report["top_failure_mode"] = reporting.infer_top_failure_mode(report)
    report["recommended_remediation_steps"] = reporting.remediation_steps_for_failure_mode(report["top_failure_mode"])
    if report["top_failure_mode"] in {"waiting_auth", "auth_session_failure"} and not report["drift_summary"].get("drift_detected"):
        report["drift_summary"] = {
            "drift_detected": True,
            "drift_types": ["auth_drift"],
            "allowed_fallback_used": [],
            "disallowed_fallback_used": [],
            "surface_match": None,
            "assertion_match": None,
            "plan_fidelity": "low",
        }
    report["run_environment"] = {
        "headless": run.get("headless", True),
        "run_mode": run.get("run_mode", "hybrid"),
        "profile_name": run.get("profile_name"),
        "app_url": run.get("app_url", ""),
    }
    report["run_health"] = {
        "event_count": len(events),
        "latest_event_type": events[-1]["event_type"] if events else None,
    }
    app_url = run.get("app_url", "")
    profile_name = run.get("profile_name")
    latest_graph = None
    if app_url:
        try:
            latest_graph = await sqlite.get_latest_context_graph(app_url, profile_name=profile_name)
        except Exception:
            latest_graph = None
    if latest_graph:
        failed_labels = [
            case.flow_name for case in cases
            if case.status in ("fail", "error", "blocked") and getattr(case, "flow_name", "")
        ]
        report["context_graph_summary"] = get_context_graph_summary(latest_graph).model_dump()
        report["context_next_checks"] = get_next_checks_for_release_scope(
            latest_graph,
            failed_journey_labels=failed_labels,
            limit=5,
        )
    else:
        report["context_graph_summary"] = None
        report["context_next_checks"] = []
    encoded_app = quote(app_url, safe="") if app_url else ""
    if encoded_app:
        report["related_v2_resources"] = [
            f"blop://v2/journey/{encoded_app}/health/7d",
            f"blop://v2/incidents/{encoded_app}/open",
            f"blop://v2/correlation/{encoded_app}/7d",
            f"blop://v2/context/{encoded_app}/latest",
        ]
    else:
        report["related_v2_resources"] = []

    run_status = report.get("status", "unknown")
    status_meta = reporting.explain_run_status(
        run_status,
        run_id=run_id,
        top_failure_mode=report["top_failure_mode"],
    )
    report["status_detail"] = status_meta["status_detail"]
    report["is_terminal"] = status_meta["is_terminal"]
    report["recommended_next_action"] = status_meta["recommended_next_action"]
    if run_status == "waiting_auth":
        report["waiting_auth_message"] = _waiting_auth_message(report["auth_provenance"], run.get("profile_name"))
        report["workflow_hint"] = report["waiting_auth_message"]
    elif run_status in ("queued", "running"):
        report["workflow_hint"] = status_meta["recommended_next_action"]
    else:
        report["recommended_next_action"] = report["context_next_checks"][0] if report["context_next_checks"] else report["recommended_next_action"]
        report["workflow_hint"] = report["recommended_next_action"]
    report["decision_summary"] = reporting.build_decision_summary(report)
    report["evidence_summary"] = reporting.build_evidence_summary(report)
    report["coverage_summary"] = reporting.build_coverage_summary(report)
    report["evidence_quality"] = reporting.build_evidence_quality(report)

    return report


async def get_run_recommendation_resource(run_id: str) -> dict:
    """Return just the release_recommendation for a run — lightweight polling target."""
    run = await sqlite.get_run(run_id)
    if not run:
        return {"error": f"Run {run_id} not found"}

    from blop.reporting.results import _compute_release_recommendation
    from blop.schemas import FailureCase

    cases = await sqlite.list_cases_for_run(run_id)
    if not cases and run.get("cases"):
        cases = [FailureCase(**c) for c in run["cases"]]

    status = run.get("status", "unknown")
    rec = _compute_release_recommendation(cases, status)

    # Apply staleness check
    import os
    from datetime import datetime, timezone
    stale_hours = int(os.getenv("BLOP_RECOMMENDATION_STALE_HOURS", "24"))
    stale = False
    completed_at = run.get("completed_at")
    if completed_at and status in ("completed", "failed"):
        try:
            completed_dt = datetime.fromisoformat(completed_at.replace("Z", "+00:00"))
            if completed_dt.tzinfo is None:
                completed_dt = completed_dt.replace(tzinfo=timezone.utc)
            age_hours = (datetime.now(timezone.utc) - completed_dt).total_seconds() / 3600
            stale = age_hours > stale_hours
        except Exception:
            pass
    rec["stale"] = stale
    if stale:
        rec["stale_reason"] = f"Run completed more than {stale_hours}h ago. Re-run for a fresh recommendation."

    return {
        "run_id": run_id,
        "status": status,
        "completed_at": completed_at,
        "release_recommendation": rec,
    }


async def list_runs(limit: int = 20, status: str | None = None) -> dict:
    runs = await sqlite.list_runs(limit=limit, status=status)
    return {
        "runs": runs,
        "total": len(runs),
        "related_v2_resources": [
            "blop://v2/contracts/tools",
        ],
    }


async def get_artifact_index_resource(run_id: str) -> dict:
    run = await sqlite.get_run(run_id)
    if not run:
        return {"error": f"Run {run_id} not found"}
    artifacts = await sqlite.list_artifacts_for_run(run_id)
    cases = await sqlite.list_cases_for_run(run_id)
    artifact_types: dict[str, int] = {}
    for a in artifacts:
        atype = a.get("artifact_type", "unknown") if isinstance(a, dict) else getattr(a, "artifact_type", "unknown")
        artifact_types[atype] = artifact_types.get(atype, 0) + 1
    return {
        "run_id": run_id,
        "status": run.get("status", "unknown"),
        "artifacts_dir": run.get("artifacts_dir", ""),
        "artifact_count": len(artifacts),
        "artifact_types": artifact_types,
        "artifacts": artifacts,
        "case_ids": [c.case_id for c in cases],
    }


async def get_flow_stability_profile_resource(flow_id: str) -> dict:
    flow = await sqlite.get_flow(flow_id)
    if not flow:
        return {"error": f"Flow {flow_id} not found"}

    cases = await sqlite.list_cases_for_flow(flow_id, limit=100)
    total = len(cases)
    if total == 0:
        return {
            "flow_id": flow_id,
            "flow_name": flow.flow_name,
            "total_runs": 0,
            "pass_rate": None,
            "failure_rate": None,
            "replay_modes": {},
            "avg_failed_step_index": None,
            "stability_score": None,
        }

    passed = sum(1 for c in cases if c.status == "pass")
    failed = sum(1 for c in cases if c.status in ("fail", "error", "blocked"))
    replay_modes: dict[str, int] = {}
    failed_step_indices: list[int] = []
    for case in cases:
        replay_modes[case.replay_mode] = replay_modes.get(case.replay_mode, 0) + 1
        if case.step_failure_index is not None:
            failed_step_indices.append(case.step_failure_index)

    pass_rate = round(passed / total, 4)
    failure_rate = round(failed / total, 4)
    avg_failed_step_index = (
        round(sum(failed_step_indices) / len(failed_step_indices), 2)
        if failed_step_indices
        else None
    )
    # Simple 0..1 proxy: high pass rate and low fallback usage means stable flow.
    fallback_ratio = replay_modes.get("goal_fallback", 0) / total
    stability_score = round(max(0.0, min(1.0, pass_rate - (fallback_ratio * 0.5))), 4)

    return {
        "flow_id": flow_id,
        "flow_name": flow.flow_name,
        "business_criticality": flow.business_criticality,
        "total_runs": total,
        "pass_rate": pass_rate,
        "failure_rate": failure_rate,
        "replay_modes": replay_modes,
        "avg_failed_step_index": avg_failed_step_index,
        "stability_score": stability_score,
    }


async def get_run_health_stream(run_id: str, limit: int = 500) -> dict:
    run = await sqlite.get_run(run_id)
    if not run:
        return {"error": f"Run {run_id} not found"}
    events = await sqlite.list_run_health_events(run_id, limit=limit)
    app_url = run.get("app_url", "")
    encoded_app = quote(app_url, safe="") if app_url else ""
    return {
        "run_id": run_id,
        "status": run.get("status", "unknown"),
        "event_count": len(events),
        "events": events,
        "related_v2_resources": [
            f"blop://v2/incidents/{encoded_app}/open",
            f"blop://v2/correlation/{encoded_app}/7d",
        ] if encoded_app else [],
    }


async def get_risk_analytics(limit_runs: int = 30) -> dict:
    runs = await sqlite.list_runs(limit=limit_runs)
    run_ids = [r["run_id"] for r in runs]

    flaky_steps: dict[str, int] = {}
    failing_transitions: dict[str, int] = {}
    business_risk: dict[str, dict[str, int]] = {
        "revenue": {"total": 0, "failed": 0},
        "activation": {"total": 0, "failed": 0},
        "retention": {"total": 0, "failed": 0},
        "support": {"total": 0, "failed": 0},
        "other": {"total": 0, "failed": 0},
    }

    for run_id in run_ids:
        cases = await sqlite.list_cases_for_run(run_id)
        for case in cases:
            bc = case.business_criticality
            if bc not in business_risk:
                bc = "other"
            business_risk[bc]["total"] += 1
            if case.status in ("fail", "error", "blocked"):
                business_risk[bc]["failed"] += 1

            if case.step_failure_index is not None and case.status in ("fail", "error", "blocked"):
                step_key = f"{case.flow_name}#step_{case.step_failure_index}"
                flaky_steps[step_key] = flaky_steps.get(step_key, 0) + 1

                # Transition proxy: flow_name + failure step index.
                transition_key = f"{case.flow_name}:transition_to_step_{case.step_failure_index}"
                failing_transitions[transition_key] = failing_transitions.get(transition_key, 0) + 1

    flaky_leaderboard = sorted(
        [{"key": k, "count": v} for k, v in flaky_steps.items()],
        key=lambda x: x["count"],
        reverse=True,
    )[:15]
    transition_leaderboard = sorted(
        [{"key": k, "count": v} for k, v in failing_transitions.items()],
        key=lambda x: x["count"],
        reverse=True,
    )[:15]

    business_risk_summary = {}
    for bc, stats in business_risk.items():
        total = stats["total"]
        failed = stats["failed"]
        failure_rate = round(failed / total, 4) if total else None
        business_risk_summary[bc] = {
            "total": total,
            "failed": failed,
            "failure_rate": failure_rate,
        }

    # Calibration summary: aggregate decision distribution across all analyzed runs
    calibration_totals: dict[str, int] = {"SHIP": 0, "INVESTIGATE": 0, "BLOCK": 0}
    app_urls = list({r.get("app_url", "") for r in runs if r.get("app_url")})
    for url in app_urls:
        records = await sqlite.list_risk_calibration(url, limit=limit_runs)
        for rec in records:
            d = rec.get("predicted_decision", "INVESTIGATE")
            if d in calibration_totals:
                calibration_totals[d] += 1
    total_calibration = sum(calibration_totals.values())
    calibration_summary = {
        "total_predictions": total_calibration,
        "decision_distribution": {
            k: {"count": v, "rate": round(v / total_calibration, 4) if total_calibration else None}
            for k, v in calibration_totals.items()
        },
    }

    return {
        "analyzed_runs": len(run_ids),
        "flaky_steps_leaderboard": flaky_leaderboard,
        "failing_transitions": transition_leaderboard,
        "business_risk": business_risk_summary,
        "calibration_summary": calibration_summary,
        "related_v2_resources": [
            "blop://v2/contracts/tools",
        ],
    }
