"""get_qa_recommendations — QA context + prioritized recommendations for an app."""

from __future__ import annotations

from typing import Literal

from blop.config import validate_app_url
from blop.engine.errors import (
    BLOP_RELEASE_NOT_FOUND,
    BLOP_RUN_NOT_FOUND,
    BLOP_URL_VALIDATION_FAILED,
    BLOP_VALIDATION_FAILED,
    tool_error,
)
from blop.engine.qa_context import build_qa_context
from blop.engine.recommendations import generate_recommendations
from blop.schemas import RecordedFlow
from blop.storage import sqlite


def _flow_to_qa_dict(flow: RecordedFlow) -> dict:
    assertion_count = len(flow.assertions_json) + len(flow.structured_assertions)
    return {
        "flow_name": flow.flow_name,
        "business_criticality": flow.business_criticality,
        "assertion_count": assertion_count,
        "steps": [s.model_dump() for s in flow.steps],
    }


def _case_rows_for_run(
    run_id: str,
    cases: list,
    started_at: str,
) -> list[dict]:
    rows: list[dict] = []
    for c in cases:
        fr = c.raw_result or (c.assertion_failures[0] if c.assertion_failures else None)
        rows.append(
            {
                "flow_name": c.flow_name,
                "status": "pass" if c.status == "pass" else "fail",
                "failure_reason": fr,
                "created_at": started_at,
                "run_id": run_id,
            }
        )
    return rows


async def get_qa_recommendations(
    app_url: str,
    release_id: str | None = None,
    scope: Literal["full", "blockers_only", "coverage_gaps"] = "full",
    lookback_runs: int = 10,
) -> dict:
    """Build QA context from stored flows and recent runs, then return structured recommendations."""
    err = validate_app_url(app_url)
    if err:
        return tool_error(err, BLOP_URL_VALIDATION_FAILED)

    safe_lookback = max(1, min(lookback_runs, 100))
    flows_full = await sqlite.list_flows_full(app_url=app_url)
    flow_dicts = [_flow_to_qa_dict(f) for f in flows_full]

    run_case_rows: list[dict] = []
    if release_id:
        brief = await sqlite.get_release_brief(release_id)
        if not brief or not isinstance(brief, dict):
            return tool_error(
                f"release_id not found: {release_id}",
                BLOP_RELEASE_NOT_FOUND,
                details={"release_id": release_id},
            )
        rid = brief.get("run_id")
        if not rid:
            return tool_error(
                "release brief has no run_id",
                BLOP_VALIDATION_FAILED,
                details={"release_id": release_id},
            )
        run = await sqlite.get_run(rid)
        if not run:
            return tool_error(
                f"run not found for release: {rid}",
                BLOP_RUN_NOT_FOUND,
                details={"release_id": release_id, "run_id": rid},
            )
        cases = await sqlite.list_cases_for_run(rid)
        started = run.get("started_at") or ""
        run_case_rows.extend(_case_rows_for_run(rid, cases, started))
    else:
        runs = await sqlite.list_runs(limit=max(safe_lookback, 30))
        matching = [r for r in runs if r.get("app_url") == app_url][:safe_lookback]
        run_ids = [r["run_id"] for r in matching]
        started_by_id = {r["run_id"]: r.get("started_at") or "" for r in matching}
        grouped = await sqlite.list_cases_for_runs(run_ids)
        for rid in run_ids:
            run_case_rows.extend(_case_rows_for_run(rid, grouped.get(rid, []), started_by_id.get(rid, "")))

    qa_ctx = await build_qa_context(app_url, flow_dicts, run_case_rows, lookback_runs=safe_lookback)
    rec_set = generate_recommendations(qa_ctx)

    if scope == "blockers_only":
        rec_set = rec_set.model_copy(
            update={
                "high_risk_gaps": [],
                "maintenance_alerts": [],
                "optimizations": [],
                "summary": f"Blocker-focused view: {len(rec_set.blockers)} blocker recommendation(s).",
            }
        )
    elif scope == "coverage_gaps":
        rec_set = rec_set.model_copy(
            update={
                "blockers": [],
                "maintenance_alerts": [],
                "optimizations": [],
                "summary": f"Coverage-gap view: {len(rec_set.high_risk_gaps)} high-risk gap(s).",
            }
        )

    out = rec_set.model_dump()
    out["qa_context"] = qa_ctx.model_dump()
    return out
