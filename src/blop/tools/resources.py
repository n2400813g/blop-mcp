"""MVP resource handlers for blop://journeys and blop://release/* URIs."""
from __future__ import annotations

from blop.engine.context_graph import find_journey_summary, get_context_graph_summary
from blop.schemas import CriticalJourney, ReleaseBrief
from blop.storage import sqlite


async def journeys_resource() -> dict:
    """Return all recorded journeys as CriticalJourney-shaped dicts."""
    flows = await sqlite.list_flows()
    journeys = []
    graph_cache: dict[tuple[str, str | None], object] = {}
    for f in flows:
        flow_obj = await sqlite.get_flow(f["flow_id"])
        criticality = getattr(flow_obj, "business_criticality", "other") if flow_obj else "other"
        goal = getattr(flow_obj, "goal", "") if flow_obj else ""
        app_url = getattr(flow_obj, "app_url", "") if flow_obj else f.get("app_url", "")
        graph_key = (app_url, None)
        if graph_key not in graph_cache and app_url:
            graph_cache[graph_key] = await sqlite.get_latest_context_graph(app_url)
        journey_summary = find_journey_summary(
            graph_cache.get(graph_key),
            flow_name=f["flow_name"],
            flow_id=f["flow_id"],
        ) if app_url else None
        canonical = CriticalJourney.model_validate({
            "journey_id": f["flow_id"],
            "journey_name": f["flow_name"],
            "why_it_matters": goal or f["flow_name"],
            "criticality_class": criticality,
            "include_in_release_gating": criticality in ("revenue", "activation"),
            "flow_id": f["flow_id"],
            "auth_required": journey_summary.auth_required if journey_summary else False,
            "confidence": 1.0 if flow_obj else 0.7,
        }).model_dump()
        journeys.append({
            **canonical,
            "created_at": f.get("created_at"),
            "coverage_status": journey_summary.coverage_status if journey_summary else "recorded",
            "entry_routes": journey_summary.entry_routes if journey_summary else [],
        })
    return {
        "journeys": journeys,
        "total": len(journeys),
    }


async def release_brief_resource(release_id: str) -> dict:
    """Return the condensed ReleaseBrief for a given release_id."""
    brief = await sqlite.get_release_brief(release_id)
    if not brief:
        return {
            "release_id": release_id,
            "error": f"No release brief found for release_id='{release_id}'. "
                     "Run run_release_check to generate one.",
        }
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
        return {
            "release_id": release_id,
            "error": f"No run linked to release_id='{release_id}'.",
            "artifacts": {},
        }

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
        return {
            "release_id": release_id,
            "error": f"No release brief found for release_id='{release_id}'.",
            "incidents": [],
        }

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
