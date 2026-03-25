"""Aggregate run data into structured RunResult report."""
from __future__ import annotations

import os
from datetime import datetime, timezone

from blop.schemas import DEFAULT_RELEASE_POLICY, FailureCase, PolicyGateResult, ReleasePolicy

_TERMINAL_RUN_STATUSES = {"completed", "failed", "cancelled", "waiting_auth"}


def _parse_iso_datetime(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except Exception:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def describe_flow_staleness(created_at: str | None) -> dict:
    threshold_days = int(os.getenv("BLOP_FLOW_STALE_DAYS", "14"))
    created_dt = _parse_iso_datetime(created_at)
    if not created_dt:
        return {
            "stale": False,
            "staleness_threshold_days": threshold_days,
            "age_days": None,
            "warning": None,
        }

    age_days = max(0.0, (datetime.now(timezone.utc) - created_dt).total_seconds() / 86400)
    stale = age_days >= threshold_days
    warning = None
    if stale:
        warning = (
            f"Recording is {int(age_days)} day(s) old, which exceeds the {threshold_days}-day staleness threshold. "
            "Refresh the flow before trusting replay results."
        )
    return {
        "stale": stale,
        "staleness_threshold_days": threshold_days,
        "age_days": round(age_days, 1),
        "warning": warning,
    }


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


def explain_run_status(status: str, *, run_id: str = "", top_failure_mode: str | None = None) -> dict:
    poll_hint = f"Poll get_test_results(run_id='{run_id}')" if run_id else "Poll get_test_results(run_id='...')"
    guidance = {
        "queued": {
            "status_detail": "Run is queued and waiting for execution to begin.",
            "recommended_next_action": poll_hint,
            "is_terminal": False,
        },
        "running": {
            "status_detail": "Run is actively replaying recorded flows and collecting evidence.",
            "recommended_next_action": poll_hint,
            "is_terminal": False,
        },
        "waiting_auth": {
            "status_detail": "Run is blocked on authentication and will not execute until a valid session is available.",
            "recommended_next_action": "Refresh auth with capture_auth_session(...) or fix the auth profile, then retry the run.",
            "is_terminal": True,
        },
        "completed": {
            "status_detail": "Run finished and the final release recommendation is ready for review.",
            "recommended_next_action": "Review the release recommendation, evidence, and remediation guidance.",
            "is_terminal": True,
        },
        "failed": {
            "status_detail": "Run terminated with at least one failure condition that needs investigation.",
            "recommended_next_action": "Inspect the top failed case and captured evidence before retrying.",
            "is_terminal": True,
        },
        "cancelled": {
            "status_detail": "Run was cancelled before it finished.",
            "recommended_next_action": "Restart the run when you are ready to continue.",
            "is_terminal": True,
        },
    }
    result = guidance.get(
        status,
        {
            "status_detail": f"Run is in an unknown state: {status}.",
            "recommended_next_action": "Inspect the run record and health events for more detail.",
            "is_terminal": False,
        },
    ).copy()
    if status == "failed":
        action_overrides = {
            "auth_session_failure": "Refresh auth, confirm the session lands inside the app, and re-run.",
            "automation_fragility": "Inspect traces/screenshots, refresh the recorded flow if the UI drifted, and re-run.",
            "environment_or_infra": "Verify the app, runtime dependencies, and network access before retrying.",
            "product_regression": "Debug the top failed case, fix the regression, and re-run.",
            "unknown": "Inspect the top failed case and captured evidence before retrying.",
        }
        result["recommended_next_action"] = action_overrides.get(
            top_failure_mode or "unknown",
            result["recommended_next_action"],
        )
    return result


def infer_top_failure_mode(report: dict) -> str:
    status = report.get("status", "unknown")
    if status == "waiting_auth":
        return "waiting_auth"

    auth = report.get("auth_provenance", {}) or {}
    session_status = auth.get("session_validation_status")
    if session_status in {"expired_session", "redirected_to_auth", "unresolved_storage_state", "missing_profile", "validation_error"}:
        return "auth_session_failure"

    failure_kinds = (report.get("coverage_summary", {}) or {}).get("failure_kinds", []) or []
    if "environment_or_infra" in failure_kinds:
        return "environment_or_infra"
    if "product_regression" in failure_kinds:
        return "product_regression"
    if "automation_fragility" in failure_kinds:
        return "automation_fragility"

    if report.get("failed_cases"):
        return "unknown"
    return "unknown"


def describe_failure_classification(report: dict) -> dict:
    mode = report.get("top_failure_mode") or infer_top_failure_mode(report)
    coverage = report.get("coverage_summary", {}) or {}
    drift = report.get("drift_summary", {}) or {}
    auth = report.get("auth_provenance", {}) or {}
    status = report.get("status", "unknown")
    auth_status_suffix = ""
    if auth.get("session_validation_status"):
        auth_status_suffix = f" ({auth['session_validation_status']})"

    rationale_map = {
        "waiting_auth": "Replay did not start because auth could not be validated before execution.",
        "auth_session_failure": (
            f"Replay evidence points to auth/session issues{auth_status_suffix}."
        ),
        "automation_fragility": "Failures align with replay drift or selector fragility rather than a confirmed product bug.",
        "environment_or_infra": "Failures look environmental, infrastructural, or runtime-related rather than journey-specific.",
        "product_regression": "Evidence points to an actual product behavior regression in a tested journey.",
        "unknown": "The run failed, but the current evidence does not support a confident classification yet.",
    }
    confidence = "medium"
    if mode in {"waiting_auth", "auth_session_failure"} and auth.get("session_validation_status"):
        confidence = "high"
    elif mode in {"environment_or_infra", "product_regression", "automation_fragility"} and coverage.get("failure_kinds"):
        confidence = "high"
    elif status in _TERMINAL_RUN_STATUSES and report.get("failed_cases"):
        confidence = "medium"
    else:
        confidence = "low"

    supporting_signals: list[str] = []
    if auth.get("session_validation_status") and auth.get("session_validation_status") != "valid":
        supporting_signals.append(f"auth:{auth['session_validation_status']}")
    for kind in coverage.get("failure_kinds", []) or []:
        supporting_signals.append(f"failure_kind:{kind}")
    if drift.get("drift_detected"):
        supporting_signals.append("drift_detected")
    for drift_type in (drift.get("drift_types", []) or [])[:2]:
        supporting_signals.append(f"drift_type:{drift_type}")

    return {
        "primary": mode,
        "confidence": confidence,
        "rationale": rationale_map.get(mode, rationale_map["unknown"]),
        "supporting_signals": supporting_signals[:5],
    }


def remediation_steps_for_failure_mode(mode: str) -> list[str]:
    steps = {
        "waiting_auth": [
            "Refresh or capture a valid auth session with capture_auth_session(...).",
            "Validate the profile against the target app_url before retrying the run.",
            "Re-run the blocked release check once auth lands inside the app.",
        ],
        "auth_session_failure": [
            "Refresh or capture auth again and confirm the session still lands in the authenticated app.",
            "Run validate_release_setup(app_url='...', profile_name='...') to verify the auth profile before replay.",
            "Retry the replay after the auth session validates cleanly.",
        ],
        "automation_fragility": [
            "Inspect the top trace and screenshots to confirm whether the UI changed or the selector drifted.",
            "Refresh the recorded flow if the app surface changed or the old flow is stale.",
            "Re-run the replay and confirm the repaired flow passes without fallback drift.",
        ],
        "environment_or_infra": [
            "Verify the app is reachable and healthy from this machine.",
            "Check runtime dependencies, browser availability, and any recent environment/config changes.",
            "Retry the run once infrastructure issues are resolved.",
        ],
        "product_regression": [
            "Debug the top failed case using its trace, screenshots, and console/network evidence.",
            "Fix the underlying product issue in the affected journey.",
            "Re-run the release check to confirm the regression is gone.",
        ],
        "unknown": [
            "Inspect the top failed case and its evidence bundle first.",
            "Use the failure classification, trace, and screenshots to decide whether this is auth, drift, infra, or product.",
            "Retry only after the likely cause is addressed.",
        ],
    }
    return steps.get(mode, steps["unknown"])


def build_failure_links(report: dict) -> dict:
    artifact_refs = list((report.get("evidence_summary", {}) or {}).get("top_artifact_refs", []) or [])
    resource_refs = list(report.get("related_v2_resources", []) or [])
    release_id = report.get("release_id")
    run_id = report.get("run_id")
    if release_id:
        for ref in [
            f"blop://release/{release_id}/brief",
            f"blop://release/{release_id}/artifacts",
            f"blop://release/{release_id}/incidents",
        ]:
            if ref not in resource_refs:
                resource_refs.append(ref)
    if run_id:
        triage_hint = f"triage_release_blocker(run_id='{run_id}')"
    else:
        triage_hint = None
    return {
        "artifacts": artifact_refs[:5],
        "resources": resource_refs[:5],
        "triage_hint": triage_hint,
    }


def _compute_release_recommendation(
    cases: list[FailureCase],
    status: str,
    *,
    policy: ReleasePolicy | None = None,
    stability_bucket: str | None = None,
) -> dict:
    """Compute a deterministic go/no-go release recommendation from run cases.

    BLO-75: Evaluates per-criticality CriticalityGate rules from a ReleasePolicy.
    BLO-76: Returns structured gate_results list and active_gating_policy block.
    BLO-77: Integrates stability blocking buckets (install_or_upgrade_failure,
            unknown_unclassified) from the top-level stability classification.

    Falls back to DEFAULT_RELEASE_POLICY when no policy is provided.
    Legacy env-var flags (BLOP_BLOCK_ON_REVENUE_FAILURE, etc.) are still
    honoured for backward compatibility.
    """
    from blop.config import (
        BLOP_BLOCK_ON_ACTIVATION_FAILURE,
        BLOP_BLOCK_ON_ANY_FAILURE,
        BLOP_BLOCK_ON_REVENUE_FAILURE,
    )

    effective_policy = policy if policy is not None else DEFAULT_RELEASE_POLICY

    failed = [c for c in cases if c.status in ("fail", "error", "blocked")]
    blockers = [c for c in failed if c.severity == "blocker"]

    # Group failures by criticality for gate evaluation
    by_criticality: dict[str, list[FailureCase]] = {}
    for c in failed:
        crit = c.business_criticality or "other"
        by_criticality.setdefault(crit, []).append(c)

    revenue_failures = by_criticality.get("revenue", [])
    activation_failures = by_criticality.get("activation", [])
    critical_journey_failures_total = len(revenue_failures) + len(activation_failures)

    critical_drifted_passes = [
        c for c in cases
        if c.status == "pass"
        and c.business_criticality in ("revenue", "activation")
        and getattr(getattr(c, "drift_summary", None), "drift_detected", False)
        and getattr(getattr(c, "drift_summary", None), "disallowed_fallback_used", [])
    ]

    # ── BLO-76: Evaluate per-criticality gates ────────────────────────────────
    gate_results: list[PolicyGateResult] = []
    decision: str = "SHIP"
    rationale_parts: list[str] = []
    contributing_gates: list[str] = []
    applied_global_flags: list[str] = []

    gated_criticalities: set[str] = set()
    for gate in effective_policy.gates:
        # Only active (enabled) gates count as "handled" for ungated-failure escalation
        if gate.enabled:
            gated_criticalities.add(gate.criticality)
        failures_for_gate = by_criticality.get(gate.criticality, [])
        fired = gate.enabled and len(failures_for_gate) >= gate.min_failures
        contribution: str = gate.on_failure if fired else "none"

        gate_results.append(PolicyGateResult(
            criticality=gate.criticality,
            gate_enabled=gate.enabled,
            failures_found=len(failures_for_gate),
            threshold=gate.min_failures,
            fired=fired,
            decision_contribution=contribution,  # type: ignore[arg-type]
            rationale=(
                f"{len(failures_for_gate)} {gate.criticality} failure(s) — gate fires {gate.on_failure}."
                if fired else
                f"Gate disabled for {gate.criticality}."
                if not gate.enabled else
                f"{len(failures_for_gate)} {gate.criticality} failure(s) — below threshold of {gate.min_failures}."
                if failures_for_gate else
                f"No {gate.criticality} failures."
            ),
        ))

        if fired:
            if contribution == "BLOCK" and decision != "BLOCK":
                decision = "BLOCK"
                contributing_gates.append(gate.criticality)
                rationale_parts.append(
                    f"{len(failures_for_gate)} {gate.criticality} failure(s) trigger BLOCK gate."
                )
            elif contribution == "INVESTIGATE" and decision == "SHIP":
                decision = "INVESTIGATE"
                contributing_gates.append(gate.criticality)
                rationale_parts.append(
                    f"{len(failures_for_gate)} {gate.criticality} failure(s) trigger INVESTIGATE gate."
                )

    # Ungated criticalities with failures still elevate SHIP → INVESTIGATE
    ungated_failures = sum(
        len(v) for k, v in by_criticality.items() if k not in gated_criticalities
    )
    if ungated_failures and decision == "SHIP":
        decision = "INVESTIGATE"
        rationale_parts.append(f"{ungated_failures} failure(s) in ungated criticality levels.")

    # Severity-based blocker escalation (always fires)
    if blockers and decision != "BLOCK":
        decision = "BLOCK"
        rationale_parts.append(f"{len(blockers)} case(s) with severity=blocker.")

    # Drifted critical passes
    if critical_drifted_passes and decision == "SHIP":
        decision = "INVESTIGATE"
        rationale_parts.append(
            f"{len(critical_drifted_passes)} critical journey pass(es) required disallowed drift."
        )

    # Global flag: block_on_any_failure
    if effective_policy.block_on_any_failure and failed and decision != "BLOCK":
        decision = "BLOCK"
        applied_global_flags.append("block_on_any_failure")
        rationale_parts.append(f"Policy block_on_any_failure — {len(failed)} failure(s) trigger BLOCK.")

    # ── BLO-77: Stability bucket integration ─────────────────────────────────
    if stability_bucket:
        if stability_bucket == "install_or_upgrade_failure" and effective_policy.block_on_install_failure:
            if decision != "BLOCK":
                decision = "BLOCK"
                applied_global_flags.append("block_on_install_failure")
                rationale_parts.append("Stability bucket install_or_upgrade_failure blocks release.")
        elif stability_bucket == "unknown_unclassified" and effective_policy.block_on_unknown_stability:
            if decision != "BLOCK":
                decision = "BLOCK"
                applied_global_flags.append("block_on_unknown_stability")
                rationale_parts.append("Stability bucket unknown_unclassified — policy requires BLOCK.")
        elif stability_bucket == "auth_session_failure" and decision == "SHIP" and failed:
            decision = "INVESTIGATE"
            rationale_parts.append("Stability bucket auth_session_failure — review before shipping.")

    # ── Legacy env-var escalation (backward compat) ───────────────────────────
    legacy_policy_blocks: list[str] = []
    if decision == "INVESTIGATE":
        if BLOP_BLOCK_ON_ANY_FAILURE and failed:
            legacy_policy_blocks.append("BLOP_BLOCK_ON_ANY_FAILURE=true")
        if BLOP_BLOCK_ON_REVENUE_FAILURE and revenue_failures:
            legacy_policy_blocks.append(
                f"BLOP_BLOCK_ON_REVENUE_FAILURE=true ({len(revenue_failures)} revenue failure(s))"
            )
        if BLOP_BLOCK_ON_ACTIVATION_FAILURE and activation_failures:
            legacy_policy_blocks.append(
                f"BLOP_BLOCK_ON_ACTIVATION_FAILURE=true ({len(activation_failures)} activation failure(s))"
            )
        if legacy_policy_blocks:
            decision = "BLOCK"
            rationale_parts.append(f"Escalated to BLOCK by env policy: {'; '.join(legacy_policy_blocks)}.")

    # Final rationale
    if rationale_parts:
        rationale = " ".join(rationale_parts)
    elif decision == "SHIP":
        rationale = "All flows passed. No failures detected."
    elif decision == "INVESTIGATE":
        rationale = f"{len(failed)} non-critical failure(s) detected. Review before shipping."
    else:
        rationale = "Failures detected. Do not ship until resolved."

    # Confidence calculation (unchanged)
    terminal_statuses = {"completed", "failed", "cancelled"}
    passed = [c for c in cases if c.status == "pass"]
    screenshot_case_count = sum(1 for c in cases if getattr(c, "screenshots", []) or [])
    trace_case_count = sum(1 for c in cases if getattr(c, "trace_path", None))
    assertion_backed_case_count = sum(
        1 for c in cases if any(bool(r.get("passed")) for r in (getattr(c, "assertion_results", []) or []))
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

    # Build all policy_gates_applied labels for the summary surface
    all_gate_labels: list[str] = []
    for g in contributing_gates:
        all_gate_labels.append(f"gate:{g}")
    for f in applied_global_flags:
        all_gate_labels.append(f)
    for label in legacy_policy_blocks:
        all_gate_labels.append(label)

    result: dict = {
        "decision": decision,
        "confidence": confidence,
        "rationale": rationale,
        "blocker_count": len(blockers),
        "critical_journey_failures": critical_journey_failures_total,
        "critical_drifted_passes": len(critical_drifted_passes),
        # BLO-76: structured gate output
        "gate_results": [gr.model_dump() for gr in gate_results],
        "active_gating_policy": {
            "policy_id": effective_policy.policy_id,
            "policy_name": effective_policy.policy_name,
            "contributing_gates": contributing_gates,
            "applied_global_flags": applied_global_flags,
            "stability_bucket_applied": stability_bucket,
        },
    }
    if all_gate_labels:
        result["policy_gates_applied"] = all_gate_labels
    return result


def _severity_label(case: FailureCase) -> str:
    """Return a human-readable label like 'BLOCKER in revenue flow: checkout'."""
    bc = getattr(case, "business_criticality", "other") or "other"
    sev = (case.severity or "none").upper()
    if bc != "other" and case.status != "pass":
        return f"{sev} in {bc} flow: {case.flow_name}"
    return sev


def _healing_confidence_label(repair_confidence: float) -> str:
    if repair_confidence >= 0.85:
        return "high"
    if repair_confidence >= 0.65:
        return "medium"
    return "low"


def _enrich_case(c: FailureCase) -> dict:
    """Return a case payload enriched with reporting metadata."""
    d = c.model_dump()
    d["artifact_paths"] = c.screenshots
    d["severity_label"] = _severity_label(c)
    d["healed_step_count"] = len(c.healed_steps or [])
    d["was_rerecorded"] = c.rerecorded
    d["failure_kind"] = _classify_failure_kind(d)
    d["healing_confidence_label"] = _healing_confidence_label(getattr(c, "repair_confidence", 0.0))
    d["healing_review_required"] = bool(
        getattr(c, "healing_decision", "none") == "propose_patch"
        or (
            getattr(c, "healing_decision", "none") == "auto_heal"
            and _healing_confidence_label(getattr(c, "repair_confidence", 0.0)) == "low"
        )
    )
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
    status_guidance = explain_run_status(
        report.get("status", "unknown"),
        run_id=report.get("run_id", ""),
        top_failure_mode=report.get("top_failure_mode"),
    )
    next_recommended_action = report.get("recommended_next_action")
    if not next_recommended_action:
        if report.get("status") in ("queued", "running", "waiting_auth"):
            next_recommended_action = status_guidance["recommended_next_action"]
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
        "critical_drifted_passes": rec.get("critical_drifted_passes", 0),
        "top_blocker_journeys": top_blockers,
        "verified_journeys": [
            case.get("flow_name", "")
            for case in (report.get("cases", []) or [])
            if case.get("status") == "pass" and case.get("flow_name")
        ][:5],
        "plan_fidelity": (report.get("drift_summary", {}) or {}).get("plan_fidelity"),
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


def build_replay_trust_summary(report: dict) -> dict:
    cases = report.get("cases", []) or []
    auto_heal_cases = [case for case in cases if case.get("healing_decision") == "auto_heal"]
    review_required_cases = [case for case in cases if case.get("healing_review_required")]
    stale_cases = [case for case in cases if (case.get("flow_staleness", {}) or {}).get("stale")]
    repair_confidences = [
        float(case.get("repair_confidence", 0.0))
        for case in cases
        if float(case.get("repair_confidence", 0.0)) > 0
    ]
    avg_repair_confidence = (
        round(sum(repair_confidences) / len(repair_confidences), 4)
        if repair_confidences
        else 0.0
    )
    if review_required_cases:
        summary = "Replay relied on low-confidence healing or proposed patches. Review the affected journey before trusting the result."
    elif stale_cases:
        summary = "Replay used stale recordings. Refresh those journeys before using this as a release gate."
    elif auto_heal_cases:
        summary = "Replay auto-healed selectors within the trust threshold."
    else:
        summary = "Replay followed the recorded journey plan without risky healing."

    return {
        "auto_heal_case_count": len(auto_heal_cases),
        "review_required_case_count": len(review_required_cases),
        "stale_recording_case_count": len(stale_cases),
        "avg_repair_confidence": avg_repair_confidence,
        "golden_path_ready": not review_required_cases and not stale_cases,
        "summary": summary,
    }


def build_drift_summary(report: dict) -> dict:
    cases = report.get("cases", []) or []
    drift_types: list[str] = []
    allowed_fallback_used: list[str] = []
    disallowed_fallback_used: list[str] = []
    surface_match = True
    assertion_match = True
    plan_fidelity = "high"

    for case in cases:
        drift = case.get("drift_summary", {}) or {}
        for item in drift.get("drift_types", []) or []:
            if item not in drift_types:
                drift_types.append(item)
        for item in drift.get("allowed_fallback_used", []) or []:
            if item not in allowed_fallback_used:
                allowed_fallback_used.append(item)
        for item in drift.get("disallowed_fallback_used", []) or []:
            if item not in disallowed_fallback_used:
                disallowed_fallback_used.append(item)
        if drift.get("surface_match") is False:
            surface_match = False
        if drift.get("assertion_match") is False:
            assertion_match = False
        if drift.get("plan_fidelity") == "low":
            plan_fidelity = "low"
        elif drift.get("plan_fidelity") == "medium" and plan_fidelity != "low":
            plan_fidelity = "medium"

    return {
        "drift_detected": bool(drift_types or allowed_fallback_used or disallowed_fallback_used),
        "drift_types": drift_types,
        "allowed_fallback_used": allowed_fallback_used,
        "disallowed_fallback_used": disallowed_fallback_used,
        "surface_match": surface_match if cases else None,
        "assertion_match": assertion_match if cases else None,
        "plan_fidelity": plan_fidelity if cases else "low",
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
    status_meta = explain_run_status(status, run_id=run.get("run_id", ""))
    extra: dict = {
        "status_detail": status_meta["status_detail"],
        "recommended_next_action": status_meta["recommended_next_action"],
        "is_terminal": status_meta["is_terminal"],
        "top_failure_mode": "waiting_auth" if status == "waiting_auth" else "unknown",
        "recommended_remediation_steps": remediation_steps_for_failure_mode("waiting_auth" if status == "waiting_auth" else "unknown"),
    }
    if status == "waiting_auth":
        extra["waiting_auth_message"] = (
            "Run is waiting for auth. The auth profile could not be resolved or the session needs to be refreshed before replay can start."
        )

    # Load the active release policy (BLO-75)
    policy: ReleasePolicy = DEFAULT_RELEASE_POLICY
    try:
        from blop.storage.sqlite import get_default_policy
        stored = await get_default_policy()
        if stored is not None:
            policy = stored
    except Exception:
        pass

    # Derive top-level stability bucket for BLO-77 integration
    stability_bucket: str | None = None
    try:
        from blop.stability import classify_report_stability
        case_dicts = [c.model_dump() for c in cases]
        failed_dicts = [c.model_dump() for c in failed]
        stab = classify_report_stability({"cases": case_dicts, "failed_cases": failed_dicts, "status": status})
        stability_bucket = stab.get("stability_bucket")
    except Exception:
        pass

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
        "release_recommendation": _compute_release_recommendation(
            cases, status, policy=policy, stability_bucket=stability_bucket
        ),
        **extra,
    }
    report["decision_summary"] = build_decision_summary(report)
    report["evidence_summary"] = build_evidence_summary(report)
    report["coverage_summary"] = build_coverage_summary(report)
    report["evidence_quality"] = build_evidence_quality(report)
    report["drift_summary"] = build_drift_summary(report)
    # BLO-77: surface stability gate summary alongside the release recommendation
    try:
        from blop.stability import build_stability_gate_summary
        report["stability_gate_summary"] = build_stability_gate_summary(
            {"failed_cases": [c.model_dump() for c in failed], "stability_bucket": stability_bucket}
        )
    except Exception:
        pass
    return report
