"""MVP resource handlers for blop://journeys and blop://release/* URIs."""

from __future__ import annotations

from blop.config import GET_TEST_RESULTS_POLL_TERMINAL_STATUSES
from blop.engine.context_graph import find_journey_summary, get_context_graph_summary
from blop.engine.errors import (
    BLOP_RELEASE_NOT_FOUND,
    BLOP_STORAGE_OPERATION_FAILED,
    BLOP_VALIDATION_FAILED,
    tool_error,
)
from blop.reporting.results import describe_flow_staleness
from blop.schemas import CriticalJourney, ReleaseBrief
from blop.storage import files as file_store
from blop.storage import sqlite

_RUN_TERMINAL_STATUSES = GET_TEST_RESULTS_POLL_TERMINAL_STATUSES
_POLL_TERMINAL_LIST = sorted(GET_TEST_RESULTS_POLL_TERMINAL_STATUSES)
_POLL_TERMINAL_STR = ", ".join(repr(s) for s in _POLL_TERMINAL_LIST)


async def run_mobile_artifacts_resource(run_id: str) -> dict:
    """Index mobile replay artifacts: screenshots, page_source XML, device logs per case."""
    rid = (run_id or "").strip()
    if not rid:
        return tool_error(
            "run_id is required",
            BLOP_VALIDATION_FAILED,
            details={"field": "run_id"},
            run_id=run_id,
            cases=[],
        )

    cases_out: list[dict] = []
    try:
        cases = await sqlite.list_cases_for_run(rid)
    except Exception as exc:
        return tool_error(
            str(exc),
            BLOP_STORAGE_OPERATION_FAILED,
            details={"stage": "run_mobile_artifacts_resource", "cause": type(exc).__name__},
            run_id=rid,
            cases=[],
        )

    for c in cases:
        if getattr(c, "platform", "web") not in ("ios", "android"):
            continue
        cases_out.append(
            {
                "case_id": c.case_id,
                "flow_id": c.flow_id,
                "flow_name": c.flow_name,
                "platform": c.platform,
                "status": c.status,
                "screenshots": list(c.screenshots or []),
                "mobile_accessibility_paths": list(getattr(c, "mobile_accessibility_paths", None) or []),
                "device_log_path": c.device_log_path,
            }
        )

    disk_page_sources: list[str] = []
    runs_root = file_store._runs_dir()
    for plat in ("ios", "android"):
        base = runs_root / "mobile" / plat / "page_source" / rid
        if base.is_dir():
            for p in sorted(base.rglob("*.xml")):
                disk_page_sources.append(str(p))

    return {
        "run_id": rid,
        "cases": cases_out,
        "page_sources_on_disk": disk_page_sources,
        "workflow_hint": "Large XML lives on disk paths above; read files directly, not inline in tool results.",
    }


async def journeys_resource(app_url: str | None = None) -> dict:
    """Return all recorded journeys as CriticalJourney-shaped dicts."""
    flows = await sqlite.list_flows()
    if app_url:
        flows = [flow for flow in flows if flow.get("app_url") == app_url]
    journeys = []
    graph_cache: dict[tuple[str, str | None], object] = {}
    stale_release_gating_count = 0
    for f in flows:
        flow_obj = None
        if "business_criticality" not in f or f.get("business_criticality") is None:
            flow_obj = await sqlite.get_flow(f["flow_id"])
        criticality = f.get("business_criticality") or getattr(flow_obj, "business_criticality", "other") or "other"
        goal = f.get("goal", "") or getattr(flow_obj, "goal", "") or ""
        current_app_url = f.get("app_url", "") or getattr(flow_obj, "app_url", "") or ""
        graph_key = (current_app_url, None)
        if graph_key not in graph_cache and current_app_url:
            graph_cache[graph_key] = await sqlite.get_latest_context_graph(current_app_url)
        journey_summary = (
            find_journey_summary(
                graph_cache.get(graph_key),
                flow_name=f["flow_name"],
                flow_id=f["flow_id"],
            )
            if current_app_url
            else None
        )
        canonical = CriticalJourney.model_validate(
            {
                "journey_id": f["flow_id"],
                "journey_name": f["flow_name"],
                "why_it_matters": goal or f["flow_name"],
                "criticality_class": criticality,
                "include_in_release_gating": criticality in ("revenue", "activation"),
                "flow_id": f["flow_id"],
                "auth_required": journey_summary.auth_required if journey_summary else False,
                "confidence": 1.0 if f.get("has_intent_contract") else 0.7,
            }
        ).model_dump()
        staleness = describe_flow_staleness(f.get("created_at"))
        if staleness["stale"] and criticality in ("revenue", "activation"):
            stale_release_gating_count += 1
        journeys.append(
            {
                **canonical,
                "app_url": current_app_url,
                "created_at": f.get("created_at"),
                "coverage_status": journey_summary.coverage_status if journey_summary else "recorded",
                "entry_routes": journey_summary.entry_routes if journey_summary else [],
                "stale_recording": staleness["stale"],
                "recording_age_days": staleness["age_days"],
                "staleness_threshold_days": staleness["staleness_threshold_days"],
                "stale_warning": staleness["warning"],
                "recommended_next_action": (
                    "Refresh this journey with record_test_flow(...) before using it for release gating."
                    if staleness["stale"]
                    else "Journey recording is recent enough for replay."
                ),
            }
        )
    workflow_hint = (
        "Review recorded release-gating journeys and run run_release_check(...) once the coverage set looks complete."
    )
    if stale_release_gating_count:
        workflow_hint = (
            f"{stale_release_gating_count} release-gating journey(s) look stale. "
            "Refresh those recordings before trusting replay failures or shipping decisions."
        )
    return {
        "journeys": journeys,
        "total": len(journeys),
        "stale_release_gating_count": stale_release_gating_count,
        "workflow_hint": workflow_hint,
    }


async def release_brief_resource(release_id: str) -> dict:
    """Return the condensed ReleaseBrief for a given release_id."""
    brief = await sqlite.get_release_brief(release_id)
    if not brief:
        return tool_error(
            f"No release brief found for release_id='{release_id}'. Run run_release_check to generate one.",
            BLOP_RELEASE_NOT_FOUND,
            details={"release_id": release_id},
            release_id=release_id,
        )
    brief = ReleaseBrief.model_validate(brief).model_dump()
    app_url = brief.get("app_url")
    if app_url:
        graph = await sqlite.get_latest_context_graph(app_url)
        if graph:
            brief["context_graph_summary"] = get_context_graph_summary(graph).model_dump()
    return brief


async def release_artifacts_resource(release_id: str) -> dict:
    """Return all artifacts for the run linked to a release_id, grouped by type."""
    brief = await sqlite.get_release_brief(release_id)
    run_id = brief.get("run_id") if brief else None

    if not run_id:
        return tool_error(
            f"No run linked to release_id='{release_id}'.",
            BLOP_RELEASE_NOT_FOUND,
            details={"release_id": release_id, "reason": "no_run_for_release"},
            release_id=release_id,
            artifacts={},
        )

    raw_artifacts = await sqlite.list_artifacts_for_run(run_id)
    grouped: dict[str, list] = {}
    for a in raw_artifacts:
        artifact_type = a.get("artifact_type", "other")
        grouped.setdefault(artifact_type, []).append(a.get("path", ""))

    return {
        "release_id": release_id,
        "run_id": run_id,
        "artifacts": grouped,
        "total": len(raw_artifacts),
    }


async def release_incidents_resource(release_id: str) -> dict:
    """Return incident clusters linked to a release_id."""
    brief = await sqlite.get_release_brief(release_id)
    app_url = brief.get("app_url") if brief else None
    run_id = brief.get("run_id") if brief else None

    if not app_url:
        return tool_error(
            f"No release brief found for release_id='{release_id}'.",
            BLOP_RELEASE_NOT_FOUND,
            details={"release_id": release_id},
            release_id=release_id,
            incidents=[],
        )

    all_clusters = await sqlite.list_open_incident_clusters(app_url)

    # Filter clusters whose evidence_refs overlap with run_id
    linked = []
    for cluster in all_clusters:
        evidence_refs = getattr(cluster, "evidence_refs", [])
        payload = cluster.model_dump()
        payload["journey_context"] = {
            "linked_journey": (cluster.metadata or {}).get("linked_journey"),
            "entry_routes": (cluster.metadata or {}).get("entry_routes", []),
            "areas": (cluster.metadata or {}).get("areas", []),
            "coverage_status": (cluster.metadata or {}).get("coverage_status", "unknown"),
            "next_checks": (cluster.metadata or {}).get("next_checks", []),
        }
        if run_id and any(f"run:{run_id}" in ref or run_id == ref for ref in evidence_refs):
            linked.append(payload)
        elif not run_id:
            linked.append(payload)

    # If no run-linked clusters found, return all open clusters for the app
    if not linked and all_clusters:
        linked = []
        for cluster in all_clusters:
            payload = cluster.model_dump()
            payload["journey_context"] = {
                "linked_journey": (cluster.metadata or {}).get("linked_journey"),
                "entry_routes": (cluster.metadata or {}).get("entry_routes", []),
                "areas": (cluster.metadata or {}).get("areas", []),
                "coverage_status": (cluster.metadata or {}).get("coverage_status", "unknown"),
                "next_checks": (cluster.metadata or {}).get("next_checks", []),
            }
            linked.append(payload)

    return {
        "release_id": release_id,
        "run_id": run_id,
        "incidents": linked,
        "total": len(linked),
    }


async def run_status_resource(run_id: str) -> dict:
    """Current state of a run — use after reconnect to resume polling.

    Returns status, flow_count, release_id, and a workflow hint.
    If the run is still active, workflow includes a poll_recipe.
    If the run is terminal, workflow points to the release brief.
    Response time target: < 50ms (pure DB read, no engine).
    """
    rid = (run_id or "").strip()
    if not rid:
        return {"error": "run_id_required", "run_id": run_id}

    run = await sqlite.get_run_summary(rid)
    if run is None:
        return {"error": "run_not_found", "run_id": rid}

    status = run["status"] or "unknown"
    release_id = run.get("release_id")

    if status in _RUN_TERMINAL_STATUSES:
        if release_id:
            next_action = f"read blop://release/{release_id}/brief for the SHIP/INVESTIGATE/BLOCK decision"
        else:
            next_action = f"read get_test_results(run_id='{rid}') for the full report"
        workflow: dict = {"next_action": next_action}
    else:
        workflow = {
            "next_action": (
                f"call get_test_results(run_id='{rid}') every 4s until status is one of: {_POLL_TERMINAL_STR}"
            ),
            "poll_recipe": {
                "tool": "get_test_results",
                "args_template": {"run_id": rid},
                "terminal_statuses": list(_POLL_TERMINAL_LIST),
                "interval_s": 4,
                "timeout_s": 900,
            },
            "progress_hint": "run is in progress",
        }

    return {
        "run_id": rid,
        "status": status,
        "release_id": release_id,
        "app_url": run.get("app_url"),
        "flow_count": run.get("flow_count", 0),
        "run_mode": run.get("run_mode", "hybrid"),
        "started_at": run.get("started_at"),
        "completed_at": run.get("completed_at"),
        "workflow": workflow,
    }
