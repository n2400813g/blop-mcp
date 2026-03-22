"""run_release_check — MVP canonical release confidence tool."""
from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from typing import Literal, Optional

from blop.config import validate_app_url
from blop.engine.logger import get_logger
from blop.storage import sqlite

_log = get_logger("tools.release_check")


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _risk_score_from_severity_counts(severity_counts: dict, decision: str) -> dict:
    """Map severity counts and decision → RiskScore."""
    blockers = severity_counts.get("blocker", 0)
    highs = severity_counts.get("high", 0)
    mediums = severity_counts.get("medium", 0)

    if blockers > 0 or decision == "BLOCK":
        value = min(100, 70 + blockers * 10 + highs * 3)
        level = "blocker"
    elif highs > 0 or decision == "INVESTIGATE":
        value = min(69, 40 + highs * 10 + mediums * 3)
        level = "high" if highs > 0 else "medium"
    elif mediums > 0:
        value = min(39, 20 + mediums * 5)
        level = "medium"
    else:
        value = max(0, 10 - severity_counts.get("none", 0))
        level = "low"

    return {"value": value, "level": level}


def _confidence_score_from_label(label: str) -> dict:
    mapping = {"high": 0.9, "medium": 0.6, "low": 0.3}
    return {"value": mapping.get(label, 0.6), "label": label}


def _summarize_selected_flows(flows: list) -> list[dict]:
    summaries: list[dict] = []
    for flow in flows[:5]:
        summaries.append(
            {
                "flow_id": getattr(flow, "flow_id", ""),
                "flow_name": getattr(flow, "flow_name", ""),
                "business_criticality": getattr(flow, "business_criticality", "other"),
                "goal": getattr(flow, "goal", ""),
            }
        )
    return summaries


def _build_release_check_result(
    run_result: dict,
    release_id: str,
    app_url: str,
) -> dict:
    """Map a run_result (from run_regression_test or get_test_results) → ReleaseCheckResult."""
    run_id = run_result.get("run_id", "")
    status = run_result.get("status", "queued")

    rec = run_result.get("release_recommendation", {})
    decision = rec.get("decision", "INVESTIGATE")
    confidence_label = rec.get("confidence", "medium")

    severity_counts = run_result.get("severity_counts", {})
    risk = _risk_score_from_severity_counts(severity_counts, decision)
    confidence = _confidence_score_from_label(confidence_label)

    # Extract blocker journeys (failed cases with blocker severity)
    cases = run_result.get("cases", [])
    blocker_journeys: list[str] = []
    for case in cases:
        if isinstance(case, dict):
            sev = case.get("severity", "")
            stat = case.get("status", "")
        else:
            sev = getattr(case, "severity", "")
            stat = getattr(case, "status", "")
        if sev == "blocker" and stat in ("fail", "error", "blocked"):
            name = case.get("flow_name") if isinstance(case, dict) else getattr(case, "flow_name", "")
            if name:
                blocker_journeys.append(name)

    # Build ActionItem list from next_actions strings
    next_actions_raw = run_result.get("next_actions", [])
    prioritized_actions = [
        {"priority": i + 1, "action": action, "owner_hint": None, "evidence_ref": run_id}
        for i, action in enumerate(next_actions_raw[:5])
    ]

    # Business impact summary
    blocker_count = severity_counts.get("blocker", 0)
    high_count = severity_counts.get("high", 0)
    if blocker_count > 0:
        business_impact = f"{blocker_count} blocker(s) in critical journeys — release at risk."
    elif high_count > 0:
        business_impact = f"{high_count} high-severity issue(s) detected — investigate before shipping."
    elif decision == "SHIP":
        business_impact = "All critical journeys passed — release confidence is high."
    else:
        business_impact = "Minor issues detected — review evidence before shipping."

    resource_links = {
        "brief": f"blop://release/{release_id}/brief",
        "artifacts": f"blop://release/{release_id}/artifacts",
        "incidents": f"blop://release/{release_id}/incidents",
    }

    return {
        "release_id": release_id,
        "run_id": run_id,
        "status": status,
        "risk": risk,
        "confidence": confidence,
        "decision": decision,
        "blocker_journeys": blocker_journeys,
        "business_impact": business_impact,
        "prioritized_actions": prioritized_actions,
        "resource_links": resource_links,
    }


async def _save_release_brief(release_check_result: dict, app_url: str) -> None:
    """Persist a ReleaseBrief to the release_snapshots table via brief_json column."""
    release_id = release_check_result["release_id"]
    run_id = release_check_result["run_id"]
    decision = release_check_result["decision"]
    risk = release_check_result["risk"]
    confidence = release_check_result["confidence"]
    blocker_journeys = release_check_result["blocker_journeys"]
    actions = release_check_result["prioritized_actions"]
    severity_counts = {}  # not directly available here; blocker count from risk
    blocker_count = len(blocker_journeys)

    brief = {
        "release_id": release_id,
        "run_id": run_id,
        "app_url": app_url,
        "created_at": _now_iso(),
        "decision": decision,
        "risk": risk,
        "confidence": confidence,
        "blocker_count": blocker_count,
        "blocker_journey_names": blocker_journeys,
        "critical_journey_failures": blocker_count,
        "top_actions": actions[:3],
    }

    try:
        await sqlite.save_release_brief(release_id, run_id, app_url, brief)
    except Exception as exc:
        _log.warning("save_release_brief_failed release_id=%s error=%s", release_id, exc, exc_info=True)


async def run_release_check(
    app_url: str,
    journey_ids: Optional[list[str]] = None,
    flow_ids: Optional[list[str]] = None,
    profile_name: Optional[str] = None,
    mode: Literal["replay", "targeted"] = "replay",
    criticality_filter: Optional[list[str]] = None,
    release_id: Optional[str] = None,
    headless: bool = True,
    run_mode: str = "hybrid",
) -> dict:
    """Run a release confidence check against critical journeys.

    This is the flagship release tool. It replays recorded journeys, computes a
    risk/confidence score, and returns a SHIP / INVESTIGATE / BLOCK decision.

    Args:
        app_url: The app to check.
        journey_ids: Deprecated alias for flow_ids.
        flow_ids: Recorded flow IDs to replay. If None, uses all recorded flows filtered
            by criticality_filter (defaults to revenue + activation).
        profile_name: Auth profile for flows that require login.
        mode: "replay" (default) uses the regression engine on recorded flows.
              "targeted" uses the evaluation engine for one-shot checking.
        criticality_filter: Criticality classes to include when journey_ids is None.
            Defaults to ["revenue", "activation"].
        release_id: Optional caller-supplied release identifier (auto-generated if omitted).
        headless: Run browser headlessly (default True).
        run_mode: Replay mode — "hybrid" (default), "strict_steps", or "goal_fallback".
    """
    url_err = validate_app_url(app_url)
    if url_err:
        return {"error": url_err}

    if journey_ids and flow_ids:
        return {"error": "Pass only one of flow_ids or journey_ids. journey_ids is a deprecated alias for flow_ids."}

    release_id = release_id or uuid.uuid4().hex
    criticality_filter = criticality_filter or ["revenue", "activation"]
    effective_flow_ids = flow_ids if flow_ids is not None else journey_ids

    if mode == "targeted":
        return await _run_targeted(app_url, effective_flow_ids, profile_name, release_id, headless)

    # Default: replay mode
    return await _run_replay(
        app_url=app_url,
        flow_ids=effective_flow_ids,
        profile_name=profile_name,
        criticality_filter=criticality_filter,
        release_id=release_id,
        headless=headless,
        run_mode=run_mode,
    )


async def _run_replay(
    app_url: str,
    flow_ids: Optional[list[str]],
    profile_name: Optional[str],
    criticality_filter: list[str],
    release_id: str,
    headless: bool,
    run_mode: str,
) -> dict:
    from blop.tools.regression import run_regression_test

    # Resolve flow_ids if not provided
    selected_flows = []
    if not flow_ids:
        all_flows = await sqlite.list_flows()
        flow_ids = []
        for f in all_flows:
            flow_obj = await sqlite.get_flow(f["flow_id"])
            if flow_obj and flow_obj.business_criticality in criticality_filter:
                flow_ids.append(f["flow_id"])
                selected_flows.append(flow_obj)
    else:
        for flow_id in flow_ids:
            flow_obj = await sqlite.get_flow(flow_id)
            if flow_obj:
                selected_flows.append(flow_obj)

    if not flow_ids:
        return {
            "error": (
                "No recorded flows found matching criticality_filter. "
                "Record journeys first with record_test_flow, or pass explicit flow_ids."
            ),
            "release_id": release_id,
        }

    run_result = await run_regression_test(
        app_url=app_url,
        flow_ids=flow_ids,
        profile_name=profile_name,
        headless=headless,
        run_mode=run_mode,
    )

    if "error" in run_result:
        return {"error": run_result["error"], "release_id": release_id}

    run_id = run_result.get("run_id", "")
    status = run_result.get("status", "queued")

    # run_regression_test is fire-and-forget; return a queued result with links
    result = {
        "release_id": release_id,
        "run_id": run_id,
        "status": status,
        "flow_count": len(flow_ids),
        "selected_flows": _summarize_selected_flows(selected_flows),
        "active_gating_policy": {
            "mode": "replay",
            "criticality_filter": criticality_filter,
            "default_release_gates": ["revenue", "activation"],
        },
        "resource_links": {
            "brief": f"blop://release/{release_id}/brief",
            "artifacts": f"blop://release/{release_id}/artifacts",
            "incidents": f"blop://release/{release_id}/incidents",
        },
        "recommended_next_step": {
            "tool": "get_test_results",
            "arguments": {"run_id": run_id},
            "reason": "Poll the queued release check until it reaches a terminal status.",
        },
        "workflow_hint": f"Release check queued. Next: get_test_results(run_id='{run_id}').",
    }

    # Best-effort persist a preliminary brief
    try:
        await sqlite.save_release_brief(release_id, run_id, app_url, {
            "release_id": release_id,
            "run_id": run_id,
            "app_url": app_url,
            "created_at": _now_iso(),
            "decision": "INVESTIGATE",
            "risk": {"value": 50, "level": "medium"},
            "confidence": {"value": 0.5, "label": "medium"},
            "blocker_count": 0,
            "blocker_journey_names": [],
            "critical_journey_failures": 0,
            "top_actions": [],
        })
    except Exception as exc:
        _log.warning("save_release_brief_failed release_id=%s error=%s", release_id, exc, exc_info=True)

    return result


async def _run_targeted(
    app_url: str,
    flow_ids: Optional[list[str]],
    profile_name: Optional[str],
    release_id: str,
    headless: bool,
) -> dict:
    from blop.tools.evaluate import evaluate_web_task

    # Build a task from journey goals or a generic one
    tasks: list[str] = []
    if flow_ids:
        for fid in flow_ids:
            flow = await sqlite.get_flow(fid)
            if flow:
                tasks.append(flow.goal)

    if not tasks:
        tasks = ["Navigate the app, check that critical user flows work end-to-end, and report any errors."]

    task = " Then: ".join(tasks[:3])  # Combine up to 3 goals

    eval_result = await evaluate_web_task(
        app_url=app_url,
        task=task,
        profile_name=profile_name,
        headless=headless,
    )

    if "error" in eval_result:
        return {"error": eval_result["error"], "release_id": release_id}

    # Synthesize a minimal RunResult-like dict to reuse _build_release_check_result
    rec = eval_result.get("release_recommendation", {})
    decision = rec.get("decision", "INVESTIGATE")
    confidence_label = rec.get("confidence", "medium")
    pf = eval_result.get("pass_fail", "error")

    severity_counts: dict = {}
    if pf == "pass":
        severity_counts = {"none": 1}
    else:
        severity_counts = {"high": 1}

    synthetic_run = {
        "run_id": eval_result.get("run_id", uuid.uuid4().hex),
        "status": "completed",
        "release_recommendation": rec,
        "severity_counts": severity_counts,
        "cases": [],
        "next_actions": [
            rec.get("rationale", "Review evaluation evidence.")
        ],
    }

    result = _build_release_check_result(synthetic_run, release_id, app_url)
    await _save_release_brief(result, app_url)

    result["mode"] = "targeted"
    result["evaluation_summary"] = eval_result.get("summary", [])
    result["selected_flows"] = []
    result["active_gating_policy"] = {
        "mode": "targeted",
        "criticality_filter": ["revenue", "activation"],
        "default_release_gates": ["revenue", "activation"],
    }
    result["recommended_next_step"] = {
        "tool": "triage_release_blocker" if decision != "SHIP" else None,
        "resource": None if decision != "SHIP" else f"blop://release/{release_id}/brief",
        "arguments": {"release_id": release_id} if decision != "SHIP" else {},
        "reason": (
            "Investigate the targeted result before making a release decision."
            if decision != "SHIP"
            else "Review the release brief and ship if it matches scope."
        ),
    }
    return result
