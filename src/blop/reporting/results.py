"""Aggregate run data into structured RunResult report."""
from __future__ import annotations

from blop.schemas import FailureCase


def _compute_release_recommendation(cases: list[FailureCase], status: str) -> dict:
    """Compute a deterministic go/no-go release recommendation from run cases.

    Base logic uses severity and business_criticality.  Policy-as-code gates
    (BLOP_BLOCK_ON_REVENUE_FAILURE, BLOP_BLOCK_ON_ACTIVATION_FAILURE,
    BLOP_BLOCK_ON_ANY_FAILURE) can escalate INVESTIGATE → BLOCK via env vars.
    """
    from blop.config import (
        BLOP_BLOCK_ON_ACTIVATION_FAILURE,
        BLOP_BLOCK_ON_ANY_FAILURE,
        BLOP_BLOCK_ON_REVENUE_FAILURE,
    )

    failed = [c for c in cases if c.status in ("fail", "error", "blocked")]
    blockers = [c for c in failed if c.severity == "blocker"]
    revenue_failures = [c for c in failed if c.business_criticality == "revenue"]
    activation_failures = [c for c in failed if c.business_criticality == "activation"]
    critical_journey_failures = revenue_failures + activation_failures

    # Base decision
    policy_blocks: list[str] = []

    if blockers or critical_journey_failures:
        decision = "BLOCK"
        rationale = (
            f"{len(blockers)} blocker(s) and {len(critical_journey_failures)} critical journey failure(s) detected. "
            "Do not ship until these are resolved."
        )
    elif failed:
        decision = "INVESTIGATE"
        rationale = f"{len(failed)} non-critical failure(s) detected. Review before shipping."
    else:
        decision = "SHIP"
        rationale = "All flows passed. No failures detected."

    # Policy gate escalations (INVESTIGATE → BLOCK only; SHIP is never silently overridden)
    if decision == "INVESTIGATE":
        if BLOP_BLOCK_ON_ANY_FAILURE and failed:
            policy_blocks.append("BLOP_BLOCK_ON_ANY_FAILURE=true")
        if BLOP_BLOCK_ON_REVENUE_FAILURE and revenue_failures:
            policy_blocks.append(f"BLOP_BLOCK_ON_REVENUE_FAILURE=true ({len(revenue_failures)} revenue failure(s))")
        if BLOP_BLOCK_ON_ACTIVATION_FAILURE and activation_failures:
            policy_blocks.append(f"BLOP_BLOCK_ON_ACTIVATION_FAILURE=true ({len(activation_failures)} activation failure(s))")
        if policy_blocks:
            decision = "BLOCK"
            rationale = rationale + f" Escalated to BLOCK by policy: {'; '.join(policy_blocks)}."

    terminal_statuses = {"completed", "failed", "cancelled"}
    if status in terminal_statuses and len(cases) >= 3:
        confidence = "high"
    elif status not in terminal_statuses:
        confidence = "low"
    else:
        confidence = "medium"

    result: dict = {
        "decision": decision,
        "confidence": confidence,
        "rationale": rationale,
        "blocker_count": len(blockers),
        "critical_journey_failures": len(critical_journey_failures),
    }
    if policy_blocks:
        result["policy_gates_applied"] = policy_blocks
    return result


def _severity_label(case: FailureCase) -> str:
    """Return a human-readable label like 'BLOCKER in revenue flow: checkout'."""
    bc = getattr(case, "business_criticality", "other") or "other"
    sev = (case.severity or "none").upper()
    if bc != "other" and case.status != "pass":
        return f"{sev} in {bc} flow: {case.flow_name}"
    return sev


def _enrich_case(c: FailureCase) -> dict:
    """Return a case payload enriched with reporting metadata."""
    d = c.model_dump()
    d["artifact_paths"] = c.screenshots
    d["severity_label"] = _severity_label(c)
    d["healed_step_count"] = len(c.healed_steps or [])
    d["was_rerecorded"] = c.rerecorded
    return d


async def build_report(run: dict, cases: list[FailureCase]) -> dict:
    severity_counts: dict[str, int] = {
        "blocker": 0, "high": 0, "medium": 0, "low": 0, "none": 0, "pass": 0, "error": 0
    }
    for c in cases:
        if c.status == "pass":
            severity_counts["pass"] = severity_counts.get("pass", 0) + 1
        elif c.status in ("error", "blocked"):
            severity_counts["error"] = severity_counts.get("error", 0) + 1
        else:
            severity_counts[c.severity] = severity_counts.get(c.severity, 0) + 1

    failed = [c for c in cases if c.status in ("fail", "error", "blocked")]

    status = run.get("status", "unknown")
    extra: dict = {}
    if status == "waiting_auth":
        extra["waiting_auth_message"] = (
            "Run is waiting for auth. The auth profile could not be resolved. "
            "Check save_auth_profile and ensure your credentials env vars are set, then retry."
        )

    # Enrich case dicts with replay metadata, severity label, and healing info
    cases_out = [_enrich_case(c) for c in cases]
    failed_out = [_enrich_case(c) for c in failed]

    return {
        "run_id": run.get("run_id", ""),
        "status": status,
        "started_at": run.get("started_at", ""),
        "completed_at": run.get("completed_at"),
        "cases": cases_out,
        "severity_counts": severity_counts,
        "failed_cases": failed_out,
        "artifacts_dir": run.get("artifacts_dir", ""),
        "run_mode": run.get("run_mode", "hybrid"),
        "next_actions": run.get("next_actions", []),
        "release_recommendation": _compute_release_recommendation(cases, status),
        **extra,
    }
