"""triage_release_blocker — root-cause evidence + next actions for a blocker."""

from __future__ import annotations

from typing import Optional

from blop.engine.context_graph import build_failure_neighborhood, get_next_checks_for_release_scope
from blop.schemas import BlockerTriage
from blop.storage import sqlite


async def triage_release_blocker(
    run_id: Optional[str] = None,
    release_id: Optional[str] = None,
    flow_id: Optional[str] = None,
    journey_id: Optional[str] = None,
    incident_cluster_id: Optional[str] = None,
    generate_remediation: bool = True,
) -> dict:
    """Provide root-cause evidence and next actions for a release blocker.

    Accepts any one (or combination) of: run_id, release_id, flow_id,
    journey_id,
    incident_cluster_id. At least one is required.

    Returns a BlockerTriage with likely_cause, evidence_summary,
    user_business_impact, recommended_action, and linked_artifacts.
    """
    if flow_id and journey_id and flow_id != journey_id:
        return {"error": "Pass only one of flow_id or journey_id. journey_id is a deprecated alias for flow_id."}

    effective_flow_id = flow_id or journey_id

    if not any([run_id, release_id, effective_flow_id, incident_cluster_id]):
        return {"error": "At least one of run_id, release_id, flow_id, journey_id, or incident_cluster_id is required."}

    # Resolve run_id from release_id if not provided
    if release_id and not run_id:
        run_id = await _resolve_run_id_from_release(release_id)

    subject_id = run_id or effective_flow_id or incident_cluster_id or release_id or "unknown"

    cases = []
    artifacts: list[str] = []
    likely_cause = "Unknown"
    evidence_parts: list[str] = []
    business_impact = "Unknown"
    recommended_action = "Review run evidence and consult engineering team."
    suggested_owner: Optional[str] = None
    graph = None
    graph_app_url: Optional[str] = None
    graph_profile_name: Optional[str] = None

    # --- Load from run_id ---
    if run_id:
        run = await sqlite.get_run(run_id)
        if run:
            cases = await sqlite.list_cases_for_run(run_id)
            run_artifacts = await sqlite.list_artifacts_for_run(run_id)
            artifacts = [a["path"] for a in run_artifacts if a.get("path")]
            graph_app_url = run.get("app_url")
            graph_profile_name = run.get("profile_name")

    # --- Load from journey_id (flow_id) ---
    elif effective_flow_id:
        journey_cases = await sqlite.list_cases_for_flow(effective_flow_id, limit=5)
        cases = journey_cases
        # Collect screenshots from recent cases
        for case in cases:
            artifacts.extend(getattr(case, "screenshots", []))
        if cases:
            graph_app_url = getattr(cases[0], "app_url", None) or graph_app_url

    # --- Load from incident_cluster_id ---
    cluster = None
    remediation = None
    if incident_cluster_id:
        cluster = await sqlite.get_incident_cluster(incident_cluster_id)
        remediation = await sqlite.get_remediation_draft(incident_cluster_id)
        if cluster:
            graph_app_url = cluster.app_url
        if not remediation and generate_remediation and cluster:
            try:
                from blop.tools.v2_surface import generate_remediation as gen_rem

                await gen_rem(
                    cluster_id=incident_cluster_id,
                    app_url=cluster.app_url,
                )
                remediation = await sqlite.get_remediation_draft(incident_cluster_id)
            except Exception:
                pass

    if graph_app_url:
        graph = await sqlite.get_latest_context_graph(graph_app_url, profile_name=graph_profile_name)

    # --- Build evidence summary ---
    failed_cases = [c for c in cases if getattr(c, "status", "") in ("fail", "error", "blocked")]
    blocker_cases = [c for c in failed_cases if getattr(c, "severity", "") == "blocker"]

    neighborhood = {}
    if cluster:
        cluster_meta = cluster.metadata or {}
        neighborhood = {
            "journey": cluster_meta.get("linked_journey"),
            "journey_key": cluster_meta.get("journey_key"),
            "entry_routes": cluster_meta.get("entry_routes", []),
            "business_criticality": cluster.affected_criticality[0] if cluster.affected_criticality else "other",
            "auth_required": cluster_meta.get("auth_required", False),
            "coverage_status": cluster_meta.get("coverage_status", "unknown"),
            "areas": cluster_meta.get("areas", []),
        }
        likely_cause = cluster.title
        evidence_parts.append(f"Incident cluster: {cluster.title} (severity: {cluster.severity})")
        evidence_parts.append(f"Affected flows: {cluster.affected_flows}")
        if neighborhood.get("journey"):
            evidence_parts.append(f"Journey neighborhood: {neighborhood['journey']}")
        if neighborhood.get("entry_routes"):
            evidence_parts.append(f"Entry routes: {', '.join(neighborhood['entry_routes'][:3])}")
        if cluster.evidence_refs:
            evidence_parts.append(f"Evidence refs: {', '.join(cluster.evidence_refs[:3])}")
    elif blocker_cases:
        top = blocker_cases[0]
        neighborhood = build_failure_neighborhood(
            graph,
            flow_name=getattr(top, "flow_name", None),
            flow_id=getattr(top, "flow_id", None),
        )
        failure_class = getattr(top, "failure_class", None) or "unknown"
        likely_cause = f"{failure_class.replace('_', ' ').title()} in {getattr(top, 'flow_name', 'journey')}"
        repro = getattr(top, "repro_steps", [])
        if repro:
            evidence_parts.append("Repro steps: " + " → ".join(str(s) for s in repro[:3]))
        if neighborhood.get("journey"):
            evidence_parts.append(f"Journey neighborhood: {neighborhood['journey']}")
        if neighborhood.get("entry_routes"):
            evidence_parts.append(f"Entry routes: {', '.join(neighborhood['entry_routes'][:3])}")
        console_errs = getattr(top, "console_errors", [])
        if console_errs:
            evidence_parts.append(f"Console errors ({len(console_errs)}): {console_errs[0][:150]}")
        assertion_fails = getattr(top, "assertion_failures", [])
        if assertion_fails:
            evidence_parts.append(f"Assertion failures: {assertion_fails[0][:150]}")
    elif failed_cases:
        top = failed_cases[0]
        neighborhood = build_failure_neighborhood(
            graph,
            flow_name=getattr(top, "flow_name", None),
            flow_id=getattr(top, "flow_id", None),
        )
        likely_cause = f"Failure in {getattr(top, 'flow_name', 'journey')}"
        repro = getattr(top, "repro_steps", [])
        if repro:
            evidence_parts.append("Repro steps: " + " → ".join(str(s) for s in repro[:3]))
        if neighborhood.get("journey"):
            evidence_parts.append(f"Journey neighborhood: {neighborhood['journey']}")

    if not evidence_parts:
        evidence_parts.append("No detailed evidence captured — check run artifacts.")

    evidence_summary = " | ".join(evidence_parts)

    # --- Business impact ---
    criticalities = set()
    for c in failed_cases:
        crit = getattr(c, "business_criticality", "other")
        if crit:
            criticalities.add(crit)
    if cluster and cluster.affected_criticality:
        criticalities.update(cluster.affected_criticality)
    if neighborhood.get("business_criticality"):
        criticalities.add(neighborhood["business_criticality"])

    if "revenue" in criticalities:
        business_impact = "Revenue-critical journey is broken — directly impacts conversions."
    elif "activation" in criticalities:
        business_impact = "Activation journey is broken — new users cannot complete onboarding."
    elif "retention" in criticalities:
        business_impact = "Retention journey is broken — may increase churn."
    elif criticalities:
        business_impact = f"Affected criticality: {', '.join(sorted(criticalities))}."
    else:
        business_impact = "Impact unknown — check flow business_criticality labels."

    # --- Recommended action ---
    if remediation:
        hypotheses = getattr(remediation, "fix_hypotheses", [])
        owner_hints = getattr(remediation, "owner_hints", [])
        if hypotheses:
            recommended_action = hypotheses[0]
        if owner_hints:
            suggested_owner = owner_hints[0]
    elif blocker_cases:
        top = blocker_cases[0]
        next_act = getattr(top, "repro_steps", [])
        if next_act:
            recommended_action = f"Investigate: {next_act[-1]}"
        else:
            recommended_action = f"Re-run debug_test_case(case_id='{getattr(top, 'case_id', '')}') for evidence."
    next_checks = get_next_checks_for_release_scope(
        graph,
        failed_journey_labels=[getattr(c, "flow_name", "") for c in failed_cases if getattr(c, "flow_name", "")],
        limit=3,
    )
    if recommended_action == "Review run evidence and consult engineering team." and next_checks:
        recommended_action = next_checks[0]
    if suggested_owner is None and neighborhood.get("areas"):
        suggested_owner = f"Team owning area '{neighborhood['areas'][0]}'"

    subject_type = (
        "run"
        if run_id
        else "flow"
        if effective_flow_id
        else "incident_cluster"
        if incident_cluster_id
        else "release"
        if release_id
        else "unknown"
    )
    business_priority = (
        "release_blocker"
        if "revenue" in criticalities or "activation" in criticalities or blocker_cases
        else "important"
        if failed_cases
        else "unknown"
    )
    confidence_note = (
        "High confidence: recurring incident cluster with linked remediation context."
        if cluster and remediation
        else "Medium confidence: direct failure evidence captured from the most relevant failed case."
        if blocker_cases or failed_cases
        else "Low confidence: evidence is sparse; inspect linked artifacts for confirmation."
    )
    top_evidence_refs = artifacts[:3]
    if cluster and cluster.evidence_refs:
        for ref in cluster.evidence_refs[:3]:
            if ref not in top_evidence_refs:
                top_evidence_refs.append(ref)
            if len(top_evidence_refs) >= 5:
                break

    canonical = BlockerTriage.model_validate(
        {
            "subject_id": subject_id,
            "likely_cause": likely_cause or "Unknown blocker",
            "evidence_summary": evidence_summary or "No detailed evidence captured.",
            "user_business_impact": business_impact or "Impact unknown.",
            "recommended_action": recommended_action or "Review the linked evidence and rerun the affected journey.",
            "suggested_owner": suggested_owner,
            "linked_artifacts": list(dict.fromkeys(artifacts[:10])),
        }
    ).model_dump()

    return {
        **canonical,
        "subject_type": subject_type,
        "evidence_summary_compact": {
            "failed_case_count": len(failed_cases),
            "blocker_case_count": len(blocker_cases),
            "top_evidence_refs": top_evidence_refs,
            "failure_neighborhood": neighborhood,
        },
        "business_priority": business_priority,
        "confidence_note": confidence_note,
        "next_checks": next_checks,
        "blocker_case_count": len(blocker_cases),
        "total_failed_cases": len(failed_cases),
        "id_contract": {
            "flow_id": effective_flow_id,
            "journey_id": journey_id,
        },
    }


async def _resolve_run_id_from_release(release_id: str) -> Optional[str]:
    """Try to find a run_id associated with a release_id."""
    try:
        brief = await sqlite.get_release_brief(release_id)
        if brief and isinstance(brief, dict):
            return brief.get("run_id")
    except Exception:
        pass
    return None
