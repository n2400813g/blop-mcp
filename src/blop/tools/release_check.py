"""run_release_check — MVP canonical release confidence tool."""

from __future__ import annotations

import os
import uuid
from datetime import datetime, timezone
from typing import Literal, Optional

from blop.config import validate_app_url, validate_mobile_replay_app_url
from blop.engine.errors import (
    BLOP_RESOURCE_NOT_FOUND,
    BLOP_URL_VALIDATION_FAILED,
    BLOP_VALIDATION_FAILED,
    merge_tool_error,
    tool_error,
)
from blop.engine.logger import get_logger
from blop.engine.smoke import run_smoke_preflight
from blop.mcp.tool_args import require_coalesced_app_identifier
from blop.reporting.results import explain_run_status
from blop.schemas import ReleaseCheckRequest
from blop.stability import build_stability_gate_summary
from blop.storage import sqlite

_log = get_logger("tools.release_check")


async def _resolve_replay_selection(
    app_url: str,
    flow_ids: Optional[list[str]],
    criticality_filter: list[str],
) -> tuple[list[str], list]:
    """Resolve flow_ids and loaded RecordedFlow objects for replay (mirrors _run_replay)."""
    if not flow_ids:
        selected_flows = await sqlite.list_flows_full(
            app_url=app_url,
            criticality_filter=criticality_filter,
        )
        return [flow.flow_id for flow in selected_flows], selected_flows
    selected_flows = await sqlite.get_flows(list(flow_ids))
    return list(flow_ids), selected_flows


def _url_validate_for_replay_flows(app_url: str, selected_flows: list) -> str | None:
    """HTTP(S) validation for web-only runs; package/bundle rules when all flows are mobile."""
    if not selected_flows:
        return validate_app_url(app_url)
    from blop.tools.regression import _regression_flow_mode

    mode, err = _regression_flow_mode(selected_flows)
    if mode == "error":
        return err
    if mode == "mobile":
        return validate_mobile_replay_app_url(app_url)
    return validate_app_url(app_url)


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


def _queued_release_check_result(
    *,
    release_id: str,
    run_id: str,
    status: str,
    flow_ids: list[str],
    selected_flows: list,
    profile_name: str | None,
    run_mode: str,
    criticality_filter: list[str],
    smoke_summary: dict | None = None,
) -> dict:
    status_meta = explain_run_status(status, run_id=run_id)
    prioritized_actions = [
        {
            "priority": 1,
            "action": "Poll the release check until it reaches a terminal status.",
            "owner_hint": None,
            "evidence_ref": run_id,
        }
    ]
    smoke_findings = list((smoke_summary or {}).get("findings", []) or [])
    if smoke_findings:
        top_finding = smoke_findings[0]
        prioritized_actions.append(
            {
                "priority": 2,
                "action": f"Review advisory smoke finding before replay results finish: {top_finding.get('message', '')}",
                "owner_hint": None,
                "evidence_ref": run_id,
            }
        )
    return {
        "release_id": release_id,
        "run_id": run_id,
        "status": status,
        "risk": {"value": 50, "level": "medium"},
        "confidence": {"value": 0.5, "label": "medium"},
        "decision": "INVESTIGATE",
        "blocker_journeys": [],
        "business_impact": "Release check queued. Critical journeys have not finished executing yet.",
        "prioritized_actions": prioritized_actions,
        "resource_links": {
            "brief": f"blop://release/{release_id}/brief",
            "artifacts": f"blop://release/{release_id}/artifacts",
            "incidents": f"blop://release/{release_id}/incidents",
        },
        "smoke_summary": smoke_summary,
        "flow_count": len(flow_ids),
        "selected_flows": _summarize_selected_flows(selected_flows),
        "execution_plan_summary": _summarize_execution_plan(selected_flows, run_mode, profile_name),
        "active_gating_policy": {
            "mode": "replay",
            "criticality_filter": criticality_filter,
            "default_release_gates": ["revenue", "activation"],
        },
        "recommended_next_step": {
            "tool": "get_test_results",
            "arguments": {"run_id": run_id},
            "reason": status_meta["recommended_next_action"],
        },
        "status_detail": status_meta["status_detail"],
        "recommended_next_action": status_meta["recommended_next_action"],
        "is_terminal": status_meta["is_terminal"],
        "workflow_hint": status_meta["recommended_next_action"],
        "stability_gate_summary": {
            "blocking_buckets": [],
            "bucket_counts": {},
            "unknown_count": 0,
            "required_follow_up_actions": ["Poll the run to collect stability bucket results."],
            "release_blocked_by_stability": False,
            "review_required_buckets": [],
        },
        "release_exit_criteria": {
            "blocking_rules": [
                "install_or_upgrade_failure in smoke coverage blocks release",
                "auth_session_failure in release-gating journeys blocks release",
                "unknown_unclassified in release smoke blocks release unless waived",
            ],
            "review_rules": [
                "stale_flow_drift reduces replay trust until the flow is refreshed",
                "selector_healing_failure reduces replay trust until the flow is refreshed",
            ],
            "release_blocked_by_stability": False,
        },
    }


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


def _summarize_execution_plan(flows: list, run_mode: str, profile_name: str | None) -> dict:
    contracts = [getattr(flow, "intent_contract", None) for flow in flows]
    return {
        "effective_run_mode": run_mode,
        "profile_name": profile_name,
        "target_surfaces": list(dict.fromkeys(contract.target_surface for contract in contracts if contract))
        or ["unknown"],
        "planning_sources": list(dict.fromkeys(contract.planning_source for contract in contracts if contract))
        or ["legacy_unstructured"],
        "legacy_flow_count": sum(1 for contract in contracts if contract is None),
    }


def _build_release_check_result(
    run_result: dict,
    release_id: str,
    app_url: str,
    smoke_summary: dict | None = None,
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
    gate_summary = build_stability_gate_summary(run_result)

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
        "smoke_summary": smoke_summary,
        "stability_gate_summary": gate_summary,
        "release_exit_criteria": {
            "blocking_rules": [
                "install_or_upgrade_failure in smoke coverage blocks release",
                "auth_session_failure in release-gating journeys blocks release",
                "unknown_unclassified in release smoke blocks release unless waived",
            ],
            "review_rules": [
                "stale_flow_drift reduces replay trust until the flow is refreshed",
                "selector_healing_failure reduces replay trust until the flow is refreshed",
            ],
            "release_blocked_by_stability": gate_summary["release_blocked_by_stability"],
        },
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
        "smoke_summary": release_check_result.get("smoke_summary"),
    }

    try:
        await sqlite.save_release_brief(release_id, run_id, app_url, brief)
    except Exception as exc:
        _log.warning("save_release_brief_failed release_id=%s error=%s", release_id, exc, exc_info=True)


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
    """Run a release confidence check against critical journeys.

    This is the flagship release tool. It replays recorded journeys, computes a
    risk/confidence score, and returns a SHIP / INVESTIGATE / BLOCK decision.

    Args:
        app_url: Web app HTTPS URL, or for mobile-only replay the package/bundle ID (same as record_test_flow).
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
        smoke_preflight: Optional advisory preflight over the app root and top flow entry URLs.
    """
    resolved, res_err = require_coalesced_app_identifier(app_url, field_label="app_url")
    if res_err:
        return res_err
    app_url = resolved
    try:
        request = ReleaseCheckRequest.model_validate(
            {
                "app_url": app_url,
                "journey_ids": journey_ids,
                "flow_ids": flow_ids,
                "profile_name": profile_name,
                "mode": mode,
                "criticality_filter": criticality_filter or ["revenue", "activation"],
                "release_id": release_id,
                "headless": headless,
                "run_mode": run_mode,
                "smoke_preflight": smoke_preflight,
            }
        )
    except Exception as exc:
        return tool_error(str(exc), BLOP_VALIDATION_FAILED, details={"stage": "release_check_request"})

    release_id = request.release_id or uuid.uuid4().hex
    effective_flow_ids = request.flow_ids if request.flow_ids is not None else request.journey_ids

    if request.mode == "targeted":
        url_err = validate_app_url(request.app_url)
        if url_err:
            return tool_error(url_err, BLOP_URL_VALIDATION_FAILED)
        result = await _run_targeted(
            request.app_url, effective_flow_ids, request.profile_name, release_id, request.headless
        )
    else:
        resolved_ids, selected_flows = await _resolve_replay_selection(
            request.app_url,
            effective_flow_ids,
            list(request.criticality_filter),
        )
        if not resolved_ids:
            return tool_error(
                (
                    "No recorded flows found matching criticality_filter. "
                    "Record journeys first with record_test_flow, or pass explicit flow_ids."
                ),
                BLOP_RESOURCE_NOT_FOUND,
                details={"release_id": release_id, "reason": "no_flows_for_criticality"},
                release_id=release_id,
            )
        url_err = _url_validate_for_replay_flows(request.app_url, selected_flows)
        if url_err:
            return tool_error(url_err, BLOP_URL_VALIDATION_FAILED, release_id=release_id)
        result = await _run_replay(
            app_url=request.app_url,
            flow_ids=effective_flow_ids,
            profile_name=request.profile_name,
            criticality_filter=request.criticality_filter,
            release_id=release_id,
            headless=request.headless,
            run_mode=request.run_mode,
            smoke_preflight=request.smoke_preflight,
            preresolved=(resolved_ids, selected_flows),
        )

    # After run completes, sync to hosted platform (best-effort, never blocks local result)
    try:
        from blop import config as _config
        from blop.sync.client import SyncClient
        from blop.sync.models import RunCasePayload, SyncRunPayload

        _project_id = _config.BLOP_PROJECT_ID
        if _config.BLOP_API_TOKEN and _config.BLOP_HOSTED_URL and _config.BLOP_PROJECT_ID:
            _sync_client = SyncClient(
                hosted_url=_config.BLOP_HOSTED_URL,
                api_token=_config.BLOP_API_TOKEN,
            )
            _run_id = result.get("run_id", "")
            _cases = result.get("cases", [])
            _sync_payload = SyncRunPayload(
                blop_mcp_run_id=_run_id,
                project_id=_project_id,
                url=request.app_url,
                runtime_contract_version=_config.BLOP_RUNTIME_CONTRACT_VERSION,
                blop_mcp_release_id=release_id,
                environment=_config.BLOP_ENV if hasattr(_config, "BLOP_ENV") else "production",
                run_cases=[
                    RunCasePayload(
                        case_id_external=str(c.get("case_id", c.get("flow_id", c.get("id", "")))),
                        status="pass" if c.get("passed") or c.get("status") == "pass" else (c.get("status") or "fail"),
                        flow_id_external=c.get("flow_id"),
                        severity=c.get("severity"),
                        result_json=c,
                    )
                    for c in _cases
                    if isinstance(c, dict)
                ],
            )
            import asyncio as _asyncio

            async def _sync_to_hosted() -> None:
                try:
                    cloud_run_id = await _sync_client.push_run(_sync_payload)
                    if not cloud_run_id or not _run_id:
                        return
                    artifact_rows = await sqlite.list_artifacts_for_run(_run_id)
                    if not artifact_rows:
                        return
                    await _sync_client.push_artifacts(cloud_run_id, artifact_rows)
                except Exception as _sync_exc:
                    _log.warning("Hosted artifact sync failed (non-fatal): %s", _sync_exc)

            _asyncio.create_task(_sync_to_hosted())
    except Exception as _exc:
        _log.warning("Failed to schedule blop sync: %s", _exc)

    return result


async def _run_replay(
    app_url: str,
    flow_ids: Optional[list[str]],
    profile_name: Optional[str],
    criticality_filter: list[str],
    release_id: str,
    headless: bool,
    run_mode: str,
    smoke_preflight: bool,
    *,
    preresolved: tuple[list[str], list] | None = None,
) -> dict:
    from blop.tools.regression import run_regression_test

    if preresolved is not None:
        flow_ids, selected_flows = preresolved
    else:
        flow_ids, selected_flows = await _resolve_replay_selection(app_url, flow_ids, criticality_filter)

    if not flow_ids:
        return tool_error(
            (
                "No recorded flows found matching criticality_filter. "
                "Record journeys first with record_test_flow, or pass explicit flow_ids."
            ),
            BLOP_RESOURCE_NOT_FOUND,
            details={"release_id": release_id, "reason": "no_flows_for_criticality"},
            release_id=release_id,
        )

    smoke_summary = None
    if smoke_preflight:
        try:
            smoke_summary = (
                await run_smoke_preflight(
                    app_url=app_url,
                    flows=selected_flows,
                    profile_name=profile_name,
                )
            ).model_dump()
        except Exception as exc:
            smoke_summary = {
                "status": "probe_error",
                "probe_count": 0,
                "findings": [
                    {"kind": "navigation_error", "severity": "high", "message": f"Smoke preflight failed: {exc}"}
                ],
                "findings_by_kind": {"navigation_error": 1},
                "probed_urls": [],
            }

    run_result = await run_regression_test(
        app_url=app_url,
        flow_ids=flow_ids,
        profile_name=profile_name,
        headless=headless,
        run_mode=run_mode,
    )

    if "error" in run_result:
        return merge_tool_error(run_result, release_id=release_id)

    run_id = run_result.get("run_id", "")
    status = run_result.get("status", "queued")

    # run_regression_test is fire-and-forget; return a queued result with links
    result = _queued_release_check_result(
        release_id=release_id,
        run_id=run_id,
        status=status,
        flow_ids=flow_ids,
        selected_flows=selected_flows,
        profile_name=profile_name,
        run_mode=run_mode,
        criticality_filter=criticality_filter,
        smoke_summary=smoke_summary,
    )

    if smoke_summary is not None:
        try:
            await sqlite.upsert_run_observation(run_id, "smoke_preflight", smoke_summary)
        except Exception as exc:
            _log.warning("save_smoke_preflight_failed run_id=%s error=%s", run_id, exc, exc_info=True)

    # Best-effort persist a preliminary brief
    try:
        await sqlite.save_release_brief(
            release_id,
            run_id,
            app_url,
            {
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
                "smoke_summary": smoke_summary,
            },
        )
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
    from blop.tools.results import get_test_results

    # Build a task from journey goals or a generic one
    tasks: list[str] = []
    if flow_ids:
        for flow in await sqlite.get_flows(list(flow_ids)):
            tasks.append(flow.goal)

    if not tasks:
        tasks = ["Navigate the app, check that critical user flows work end-to-end, and report any errors."]

    task = " Then: ".join(tasks[:3])  # Combine up to 3 goals
    max_steps = int(os.getenv("BLOP_TARGETED_MAX_STEPS", "40"))

    eval_result = await evaluate_web_task(
        task=task,
        app_url=app_url,
        profile_name=profile_name,
        headless=headless,
        max_steps=max_steps,
    )

    if "error" in eval_result:
        return merge_tool_error(eval_result, release_id=release_id)

    run_id = eval_result.get("run_id")
    if not run_id:
        return tool_error(
            "Targeted evaluation did not return a run_id.",
            BLOP_VALIDATION_FAILED,
            details={"release_id": release_id, "stage": "targeted_eval"},
            release_id=release_id,
        )

    detailed_result = await get_test_results(run_id)
    if "error" in detailed_result:
        return merge_tool_error(detailed_result, release_id=release_id)

    result = _build_release_check_result(detailed_result, release_id, app_url)
    result.update(detailed_result)
    rec = result.get("release_recommendation", {})
    decision = rec.get("decision", "INVESTIGATE")
    await _save_release_brief(result, app_url)
    status_meta = explain_run_status(
        result["status"],
        run_id=result["run_id"],
        top_failure_mode=result.get("top_failure_mode", "unknown"),
    )

    result["release_id"] = release_id
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
    result["status_detail"] = status_meta["status_detail"]
    result["recommended_next_action"] = status_meta["recommended_next_action"]
    result["is_terminal"] = status_meta["is_terminal"]
    result["workflow_hint"] = status_meta["recommended_next_action"]
    result["stability_gate_summary"] = build_stability_gate_summary(result)
    return result


async def refresh_release_brief_after_run(run_id: str) -> None:
    """If *run_id* is linked from release_snapshots, persist an updated ReleaseBrief."""
    link = await sqlite.get_release_id_for_run(run_id)
    if not link:
        return
    from blop.tools.results import get_test_results

    detailed = await get_test_results(run_id)
    if "error" in detailed:
        return
    release_id = link["release_id"]
    app_url = link["app_url"] or detailed.get("app_url") or ""
    smoke_summary = await sqlite.get_run_observation(run_id, "smoke_preflight")
    if isinstance(smoke_summary, dict):
        smoke_summary.pop("updated_at", None)
    result = _build_release_check_result(detailed, release_id, app_url, smoke_summary=smoke_summary)
    await _save_release_brief(result, app_url)
