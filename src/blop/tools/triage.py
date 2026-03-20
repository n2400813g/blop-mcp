"""triage_release_blocker — root-cause evidence + next actions for a blocker."""
from __future__ import annotations

from typing import Optional

from blop.storage import sqlite


async def triage_release_blocker(
    run_id: Optional[str] = None,
    release_id: Optional[str] = None,
    journey_id: Optional[str] = None,
    incident_cluster_id: Optional[str] = None,
    generate_remediation: bool = True,
) -> dict:
    """Provide root-cause evidence and next actions for a release blocker.

    Accepts any one (or combination) of: run_id, release_id, journey_id,
    incident_cluster_id. At least one is required.

    Returns a BlockerTriage with likely_cause, evidence_summary,
    user_business_impact, recommended_action, and linked_artifacts.
    """
    if not any([run_id, release_id, journey_id, incident_cluster_id]):
        return {
            "error": "At least one of run_id, release_id, journey_id, or incident_cluster_id is required."
        }

    # Resolve run_id from release_id if not provided
    if release_id and not run_id:
        run_id = await _resolve_run_id_from_release(release_id)

    subject_id = run_id or journey_id or incident_cluster_id or release_id or "unknown"

    cases = []
    artifacts: list[str] = []
    likely_cause = "Unknown"
    evidence_parts: list[str] = []
    business_impact = "Unknown"
    recommended_action = "Review run evidence and consult engineering team."
    suggested_owner: Optional[str] = None

    # --- Load from run_id ---
    if run_id:
        run = await sqlite.get_run(run_id)
        if run:
            cases = await sqlite.list_cases_for_run(run_id)
            run_artifacts = await sqlite.list_artifacts_for_run(run_id)
            artifacts = [a["path"] for a in run_artifacts if a.get("path")]

    # --- Load from journey_id (flow_id) ---
    elif journey_id:
        journey_cases = await sqlite.list_cases_for_flow(journey_id, limit=5)
        cases = journey_cases
        # Collect screenshots from recent cases
        for case in cases:
            artifacts.extend(getattr(case, "screenshots", []))

    # --- Load from incident_cluster_id ---
    cluster = None
    remediation = None
    if incident_cluster_id:
        cluster = await sqlite.get_incident_cluster(incident_cluster_id)
        remediation = await sqlite.get_remediation_draft(incident_cluster_id)
        if not remediation and generate_remediation and cluster:
            try:
                from blop.tools.v2_surface import generate_remediation as gen_rem
                rem_result = await gen_rem(
                    cluster_id=incident_cluster_id,
                    app_url=cluster.app_url,
                )
                remediation = await sqlite.get_remediation_draft(incident_cluster_id)
            except Exception:
                pass

    # --- Build evidence summary ---
    failed_cases = [
        c for c in cases
        if getattr(c, "status", "") in ("fail", "error", "blocked")
    ]
    blocker_cases = [c for c in failed_cases if getattr(c, "severity", "") == "blocker"]

    if cluster:
        likely_cause = cluster.title
        evidence_parts.append(f"Incident cluster: {cluster.title} (severity: {cluster.severity})")
        evidence_parts.append(f"Affected flows: {cluster.affected_flows}")
        if cluster.evidence_refs:
            evidence_parts.append(f"Evidence refs: {', '.join(cluster.evidence_refs[:3])}")
    elif blocker_cases:
        top = blocker_cases[0]
        failure_class = getattr(top, "failure_class", None) or "unknown"
        likely_cause = f"{failure_class.replace('_', ' ').title()} in {getattr(top, 'flow_name', 'journey')}"
        repro = getattr(top, "repro_steps", [])
        if repro:
            evidence_parts.append("Repro steps: " + " → ".join(str(s) for s in repro[:3]))
        console_errs = getattr(top, "console_errors", [])
        if console_errs:
            evidence_parts.append(f"Console errors ({len(console_errs)}): {console_errs[0][:150]}")
        assertion_fails = getattr(top, "assertion_failures", [])
        if assertion_fails:
            evidence_parts.append(f"Assertion failures: {assertion_fails[0][:150]}")
    elif failed_cases:
        top = failed_cases[0]
        likely_cause = f"Failure in {getattr(top, 'flow_name', 'journey')}"
        repro = getattr(top, "repro_steps", [])
        if repro:
            evidence_parts.append("Repro steps: " + " → ".join(str(s) for s in repro[:3]))

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

    return {
        "subject_id": subject_id,
        "likely_cause": likely_cause,
        "evidence_summary": evidence_summary,
        "user_business_impact": business_impact,
        "recommended_action": recommended_action,
        "suggested_owner": suggested_owner,
        "linked_artifacts": artifacts[:10],
        "blocker_case_count": len(blocker_cases),
        "total_failed_cases": len(failed_cases),
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
