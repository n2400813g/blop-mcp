"""MVP resource handlers for blop://journeys and blop://release/* URIs."""
from __future__ import annotations

from blop.storage import sqlite


async def journeys_resource() -> dict:
    """Return all recorded journeys as CriticalJourney-shaped dicts."""
    flows = await sqlite.list_flows()
    journeys = []
    for f in flows:
        flow_obj = await sqlite.get_flow(f["flow_id"])
        criticality = getattr(flow_obj, "business_criticality", "other") if flow_obj else "other"
        goal = getattr(flow_obj, "goal", "") if flow_obj else ""
        journeys.append({
            "journey_id": f["flow_id"],
            "journey_name": f["flow_name"],
            "why_it_matters": goal or f["flow_name"],
            "criticality_class": criticality,
            "include_in_release_gating": criticality in ("revenue", "activation"),
            "flow_id": f["flow_id"],
            "created_at": f.get("created_at"),
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
        if run_id and run_id in evidence_refs:
            linked.append(cluster.model_dump())
        elif not run_id:
            linked.append(cluster.model_dump())

    # If no run-linked clusters found, return all open clusters for the app
    if not linked and all_clusters:
        linked = [c.model_dump() for c in all_clusters]

    return {
        "release_id": release_id,
        "run_id": run_id,
        "incidents": linked,
        "total": len(linked),
    }
