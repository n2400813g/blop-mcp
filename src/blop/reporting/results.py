"""Aggregate run data into structured RunResult report."""
from __future__ import annotations

from blop.schemas import FailureCase


def _artifact_paths_for_case(case: dict) -> list[str]:
    paths: list[str] = []
    for path in case.get("artifact_paths", []) or []:
        if path and path not in paths:
            paths.append(path)
    trace_path = case.get("trace_path")
    if trace_path and trace_path not in paths:
        paths.append(trace_path)
    return paths


def _extract_observed_assertions(case: dict) -> list[str]:
    observed: list[str] = []
    for result in case.get("assertion_results", []) or []:
        if not result.get("passed", False):
            continue
        assertion = str(result.get("assertion", "")).strip()
        if assertion and assertion not in observed:
            observed.append(assertion)
    return observed


def _classify_failure_kind(case: dict) -> str:
    failure_class = (case.get("failure_class") or "").strip()
    mapping = {
        "product_bug": "product_regression",
        "test_fragility": "automation_fragility",
        "auth_failure": "auth_session_failure",
        "env_issue": "environment_or_infra",
    }
    return mapping.get(failure_class, "unknown")


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
    passed = [c for c in cases if c.status == "pass"]
    screenshot_case_count = sum(1 for c in cases if getattr(c, "screenshots", []) or [])
    trace_case_count = sum(1 for c in cases if getattr(c, "trace_path", None))
    assertion_backed_case_count = sum(
        1 for c in cases if any(bool(result.get("passed")) for result in (getattr(c, "assertion_results", []) or []))
    )
    fragility_failures = sum(1 for c in failed if getattr(c, "failure_class", None) == "test_fragility")
    terminal = status in terminal_statuses
    strong_evidence = (
        terminal
        and len(cases) >= 3
        and len(passed) >= max(1, len(cases) - len(failed))
        and screenshot_case_count >= max(1, len(cases) - len(failed))
        and trace_case_count >= max(1, len(cases) - len(failed))
        and assertion_backed_case_count >= max(1, len(passed))
    )
    if strong_evidence and fragility_failures == 0:
        confidence = "high"
    elif terminal:
        confidence = "medium"
    else:
        confidence = "low"

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
    d["failure_kind"] = _classify_failure_kind(d)
    return d


def build_decision_summary(report: dict) -> dict:
    """Build a compact decision-first summary from a run report payload."""
    rec = report.get("release_recommendation", {}) or {}
    failed_cases = report.get("failed_cases", []) or []
    top_blockers = [
        case.get("flow_name", "")
        for case in failed_cases
        if case.get("severity") == "blocker" and case.get("status") in ("fail", "error", "blocked")
    ]
    top_blockers = [name for name in top_blockers if name][:3]

    decision = rec.get("decision", "INVESTIGATE")
    if report.get("status") in ("queued", "running"):
        next_recommended_action = f"Poll get_test_results(run_id='{report.get('run_id', '')}') until the run completes."
    elif decision == "BLOCK":
        next_recommended_action = "Investigate the top blocker and re-run after a fix."
    elif decision == "INVESTIGATE":
        next_recommended_action = "Review failed cases and inspect the highest-signal evidence."
    else:
        next_recommended_action = "Review the final release brief and ship if it matches release scope."

    return {
        "decision": decision,
        "confidence": rec.get("confidence", "medium"),
        "blocker_count": rec.get("blocker_count", 0),
        "critical_journey_failures": rec.get("critical_journey_failures", 0),
        "top_blocker_journeys": top_blockers,
        "verified_journeys": [
            case.get("flow_name", "")
            for case in (report.get("cases", []) or [])
            if case.get("status") == "pass" and case.get("flow_name")
        ][:5],
        "next_recommended_action": next_recommended_action,
    }


def build_evidence_summary(report: dict) -> dict:
    """Build a compact evidence summary from a run report payload."""
    failed_cases = report.get("failed_cases", []) or []
    all_cases = report.get("cases", []) or []
    evidence_cases = failed_cases or [case for case in all_cases if case.get("status") == "pass"]
    artifact_refs: list[str] = []
    console_error_count = 0
    network_error_count = 0
    healed_case_count = 0
    trace_count = 0
    screenshot_count = 0

    for case in all_cases:
        if case.get("trace_path"):
            trace_count += 1
        screenshot_count += len(case.get("artifact_paths", []) or [])

    for case in evidence_cases[:5]:
        console_error_count += len(case.get("console_errors", []) or [])
        network_error_count += len(case.get("network_errors", []) or [])
        if case.get("healed_step_count", 0):
            healed_case_count += 1
        for path in _artifact_paths_for_case(case)[:2]:
            if path and path not in artifact_refs:
                artifact_refs.append(path)
            if len(artifact_refs) >= 5:
                break
        if len(artifact_refs) >= 5:
            break

    return {
        "failed_case_count": len(failed_cases),
        "console_error_count": console_error_count,
        "network_error_count": network_error_count,
        "healed_case_count": healed_case_count,
        "trace_count": trace_count,
        "screenshot_count": screenshot_count,
        "top_artifact_refs": artifact_refs,
    }


def build_coverage_summary(report: dict) -> dict:
    """Summarize what product surface was actually verified during the run."""
    cases = report.get("cases", []) or []
    passed_cases = [case for case in cases if case.get("status") == "pass"]
    failed_cases = [case for case in cases if case.get("status") in ("fail", "error", "blocked")]
    observed_assertions: list[str] = []
    proof_artifact_refs: list[str] = []
    failure_kinds: list[str] = []

    for case in passed_cases:
        for assertion in _extract_observed_assertions(case):
            if assertion not in observed_assertions:
                observed_assertions.append(assertion)
            if len(observed_assertions) >= 5:
                break
        for path in _artifact_paths_for_case(case):
            if path not in proof_artifact_refs:
                proof_artifact_refs.append(path)
            if len(proof_artifact_refs) >= 5:
                break
        if len(observed_assertions) >= 5 and len(proof_artifact_refs) >= 5:
            break

    for case in failed_cases:
        kind = case.get("failure_kind", "unknown")
        if kind not in failure_kinds:
            failure_kinds.append(kind)

    verified_journeys = []
    blocked_journeys = []
    for case in passed_cases:
        name = case.get("flow_name", "")
        if name and name not in verified_journeys:
            verified_journeys.append(name)
    for case in failed_cases:
        name = case.get("flow_name", "")
        if name and name not in blocked_journeys:
            blocked_journeys.append(name)

    return {
        "total_cases": len(cases),
        "passed_case_count": len(passed_cases),
        "failed_case_count": len(failed_cases),
        "verified_journeys": verified_journeys[:5],
        "blocked_journeys": blocked_journeys[:5],
        "observed_assertions": observed_assertions[:5],
        "proof_artifact_refs": proof_artifact_refs[:5],
        "failure_kinds": failure_kinds[:3],
    }


def build_evidence_quality(report: dict) -> dict:
    """Describe how much proof exists behind the run result."""
    cases = report.get("cases", []) or []
    total_cases = len(cases)
    screenshot_case_count = sum(1 for case in cases if case.get("artifact_paths"))
    trace_case_count = sum(1 for case in cases if case.get("trace_path"))
    assertion_backed_case_count = sum(1 for case in cases if _extract_observed_assertions(case))
    failure_kinds = {
        case.get("failure_kind", "unknown")
        for case in cases
        if case.get("status") in ("fail", "error", "blocked")
    }
    confidence_drivers: list[str] = []
    if screenshot_case_count:
        confidence_drivers.append(f"{screenshot_case_count}/{total_cases or 1} case(s) include screenshots")
    if trace_case_count:
        confidence_drivers.append(f"{trace_case_count}/{total_cases or 1} case(s) include traces")
    if assertion_backed_case_count:
        confidence_drivers.append(f"{assertion_backed_case_count}/{total_cases or 1} case(s) include passed assertions")
    if "automation_fragility" in failure_kinds:
        confidence_drivers.append("Some failures appear to be automation fragility, not confirmed product regressions")
    if not confidence_drivers:
        confidence_drivers.append("Evidence capture is sparse")

    return {
        "screenshots_present": screenshot_case_count > 0,
        "traces_present": trace_case_count > 0,
        "screenshot_case_count": screenshot_case_count,
        "trace_case_count": trace_case_count,
        "assertion_backed_case_count": assertion_backed_case_count,
        "confidence_drivers": confidence_drivers,
    }


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

    report = {
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
    report["decision_summary"] = build_decision_summary(report)
    report["evidence_summary"] = build_evidence_summary(report)
    report["coverage_summary"] = build_coverage_summary(report)
    report["evidence_quality"] = build_evidence_quality(report)
    return report
