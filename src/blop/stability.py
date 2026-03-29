"""Shared stability bucket classification and summaries."""

from __future__ import annotations

from collections import Counter
from typing import Any

STABILITY_BUCKETS = (
    "auth_session_failure",
    "stale_flow_drift",
    "selector_healing_failure",
    "environment_runtime_misconfig",
    "install_or_upgrade_failure",
    "network_transient_infra",
    "product_regression",
    "unknown_unclassified",
)

_DEFAULT_RECOVERY_RECIPES = {
    "auth_session_failure": [
        "Refresh or capture a valid auth session.",
        "Validate the auth profile against the target app before replay.",
        "Retry only after auth validation succeeds.",
    ],
    "stale_flow_drift": [
        "Refresh the stale recorded flow.",
        "Re-run replay and confirm the same journey still fails.",
        "Treat any old failure as untrusted until the recording is refreshed.",
    ],
    "selector_healing_failure": [
        "Inspect the failed step and repair evidence.",
        "Refresh the flow if the UI drifted or the selector strategy changed.",
        "Re-run and confirm the journey passes without risky healing.",
    ],
    "environment_runtime_misconfig": [
        "Fix the runtime or environment precondition first.",
        "Re-run validation to confirm the environment is healthy.",
        "Retry replay or release checks only after the runtime is stable.",
    ],
    "install_or_upgrade_failure": [
        "Fix the install, upgrade, or browser/runtime setup problem first.",
        "Repeat the clean-environment smoke path.",
        "Do not ship until installation and entrypoints are healthy again.",
    ],
    "network_transient_infra": [
        "Verify the app and network path from this machine.",
        "Check reachability, DNS, and transient upstream failures.",
        "Retry once infrastructure health is restored.",
    ],
    "product_regression": [
        "Inspect the failing journey evidence.",
        "Fix the underlying product behavior.",
        "Re-run release validation to confirm the regression is gone.",
    ],
    "unknown_unclassified": [
        "Inspect the top failed case and missing evidence first.",
        "Collect the missing signals needed for confident classification.",
        "Retry only after the likely cause is narrowed down.",
    ],
}


def describe_flow_staleness(created_at: str | None) -> dict:
    from blop.reporting.results import describe_flow_staleness as _describe_flow_staleness

    return _describe_flow_staleness(created_at)


def classify_case_stability(
    case: dict[str, Any],
    *,
    auth_provenance: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Classify a single run case into a canonical stability bucket."""
    auth_provenance = auth_provenance or {}
    failure_class = str(case.get("failure_class") or "").strip()
    failure_reason_codes = [str(code or "").strip().lower() for code in case.get("failure_reason_codes", []) or []]
    network_errors = [str(item or "").lower() for item in case.get("network_errors", []) or []]
    console_errors = [str(item or "").lower() for item in case.get("console_errors", []) or []]
    drift_summary = case.get("drift_summary", {}) or {}
    flow_staleness = case.get("flow_staleness", {}) or {}
    healing_decision = str(case.get("healing_decision") or "none")
    replay_mode = str(case.get("replay_mode") or "")
    auth_status = str(auth_provenance.get("session_validation_status") or "")

    evidence: list[str] = []
    bucket = "unknown_unclassified"
    confidence = "low"

    if auth_status in {
        "expired_session",
        "redirected_to_auth",
        "missing_profile",
        "unresolved_storage_state",
        "validation_error",
    }:
        bucket = "auth_session_failure"
        confidence = "high"
        evidence.append(f"auth:{auth_status}")
    elif flow_staleness.get("stale"):
        bucket = "stale_flow_drift"
        confidence = "high"
        evidence.append("flow:stale_recording")
    elif "repair_rejected" in failure_reason_codes or healing_decision == "propose_patch":
        bucket = "selector_healing_failure"
        confidence = "high"
        evidence.append("healing:repair_rejected")
    elif failure_class == "test_fragility":
        bucket = "selector_healing_failure"
        confidence = "medium"
        evidence.append("failure_class:test_fragility")
    elif failure_class == "auth_failure":
        bucket = "auth_session_failure"
        confidence = "high"
        evidence.append("failure_class:auth_failure")
    elif failure_class == "product_bug":
        bucket = "product_regression"
        confidence = "high"
        evidence.append("failure_class:product_bug")
    elif failure_class in {"env_issue", "install_failure"}:
        if _looks_like_install_issue(failure_reason_codes, network_errors, console_errors):
            bucket = "install_or_upgrade_failure"
            confidence = "medium"
            evidence.append("environment:install_or_upgrade")
        elif _looks_like_network_issue(failure_reason_codes, network_errors, console_errors):
            bucket = "network_transient_infra"
            confidence = "medium"
            evidence.append("environment:network_or_infra")
        else:
            bucket = "environment_runtime_misconfig"
            confidence = "medium"
            evidence.append(f"failure_class:{failure_class}")
    elif failure_class == "startup_failure":
        bucket = "install_or_upgrade_failure"
        confidence = "medium"
        evidence.append("failure_class:startup_failure")
    elif failure_class == "navigation_crash":
        bucket = "product_regression"
        confidence = "medium"
        evidence.append("failure_class:navigation_crash")
    elif _looks_like_install_issue(failure_reason_codes, network_errors, console_errors):
        bucket = "install_or_upgrade_failure"
        confidence = "medium"
        evidence.append("signals:install_issue")
    elif _looks_like_network_issue(failure_reason_codes, network_errors, console_errors):
        bucket = "network_transient_infra"
        confidence = "medium"
        evidence.append("network:error_observed")
    elif "plan_drift" in (drift_summary.get("drift_types", []) or []) and replay_mode in {
        "hybrid_repair",
        "agent_repair",
    }:
        bucket = "selector_healing_failure"
        confidence = "medium"
        evidence.append("drift:plan_drift")
    elif case.get("status") in ("fail", "error"):
        bucket = "product_regression"
        confidence = "low"
        evidence.append("status:fail_no_other_signals")

    if healing_decision == "auto_heal" and bucket == "selector_healing_failure":
        evidence.append("healing:auto_heal")
    for code in failure_reason_codes[:3]:
        if code and f"reason:{code}" not in evidence:
            evidence.append(f"reason:{code}")

    payload = {
        "stability_bucket": bucket,
        "bucket_confidence": confidence,
        "bucket_evidence": evidence[:5],
        "bucket_recovery_recipe": list(_DEFAULT_RECOVERY_RECIPES[bucket]),
    }
    if bucket == "unknown_unclassified":
        payload["unknown_classification_gaps"] = infer_unknown_classification_gaps(
            case, auth_provenance=auth_provenance
        )
    return payload


def classify_report_stability(report: dict[str, Any]) -> dict[str, Any]:
    """Classify a full run report into a single top-level stability bucket."""
    status = str(report.get("status") or "")
    auth_provenance = report.get("auth_provenance", {}) or {}
    failed_cases = report.get("failed_cases", []) or []
    cases = failed_cases or (report.get("cases", []) or [])
    all_cases = report.get("cases", []) or []
    if all_cases and not failed_cases and all(case.get("status") == "pass" for case in all_cases):
        return {
            "stability_bucket": None,
            "bucket_confidence": "high",
            "bucket_evidence": [],
            "bucket_recovery_recipe": [],
            "unknown_classification_gaps": [],
        }

    if status == "waiting_auth":
        bucket = "auth_session_failure"
        payload = {
            "stability_bucket": bucket,
            "bucket_confidence": "high",
            "bucket_evidence": [f"run_status:{status}"],
            "bucket_recovery_recipe": list(_DEFAULT_RECOVERY_RECIPES[bucket]),
        }
        payload["unknown_classification_gaps"] = []
        return payload

    classified_cases = [classify_case_stability(case, auth_provenance=auth_provenance) for case in cases]
    ranking = {
        "auth_session_failure": 0,
        "install_or_upgrade_failure": 1,
        "network_transient_infra": 2,
        "environment_runtime_misconfig": 3,
        "stale_flow_drift": 4,
        "selector_healing_failure": 5,
        "product_regression": 6,
        "unknown_unclassified": 7,
    }
    if classified_cases:
        primary = sorted(
            classified_cases,
            key=lambda item: ranking.get(item["stability_bucket"], 999),
        )[0]
        primary.setdefault("unknown_classification_gaps", [])
        return primary

    return {
        "stability_bucket": "unknown_unclassified",
        "bucket_confidence": "low",
        "bucket_evidence": ["report:missing_failed_cases"],
        "bucket_recovery_recipe": list(_DEFAULT_RECOVERY_RECIPES["unknown_unclassified"]),
        "unknown_classification_gaps": [
            "No failed or blocked cases were available for classification.",
            "No auth/session failure evidence was captured.",
            "No drift or environment evidence was captured.",
        ],
    }


def classify_validation_issue(check_name: str, message: str, *, passed: bool) -> dict[str, Any]:
    lowered_name = (check_name or "").lower()
    lowered_message = (message or "").lower()
    if passed:
        return {
            "stability_bucket": None,
            "bucket_confidence": "high",
            "bucket_evidence": [],
            "bucket_recovery_recipe": [],
        }

    if "auth_profile" in lowered_name:
        bucket = "auth_session_failure"
    elif "chromium" in lowered_name:
        bucket = "install_or_upgrade_failure"
    elif "app_url_reachable" in lowered_name or any(
        token in lowered_message
        for token in (
            "connection refused",
            "timed out",
            "name or service not known",
            "temporary failure",
            "not reachable",
        )
    ):
        bucket = "network_transient_infra"
    else:
        bucket = "environment_runtime_misconfig"

    return {
        "stability_bucket": bucket,
        "bucket_confidence": "high" if bucket != "network_transient_infra" else "medium",
        "bucket_evidence": [f"check:{check_name}"],
        "bucket_recovery_recipe": list(_DEFAULT_RECOVERY_RECIPES[bucket]),
    }


def build_validation_stability_readiness(result: dict[str, Any]) -> dict[str, Any]:
    bucket_counter: Counter[str] = Counter()
    bucketed_issues: list[dict[str, Any]] = []
    for issue in list(result.get("bucketed_blockers", []) or []) + list(result.get("bucketed_warnings", []) or []):
        bucket = issue.get("stability_bucket")
        if bucket:
            bucket_counter[bucket] += 1
        bucketed_issues.append(issue)

    primary_bucket = None
    if bucketed_issues:
        primary_bucket = bucketed_issues[0].get("stability_bucket")

    return {
        "status": result.get("status", "warnings"),
        "primary_bucket": primary_bucket,
        "bucket_counts": dict(bucket_counter),
        "blocking_bucket_count": len(result.get("bucketed_blockers", []) or []),
        "warning_bucket_count": len(result.get("bucketed_warnings", []) or []),
        "ready_for_release_gating": result.get("status") == "ready" and not bucketed_issues,
    }


def infer_unknown_classification_gaps(
    case: dict[str, Any],
    *,
    auth_provenance: dict[str, Any] | None = None,
) -> list[str]:
    auth_provenance = auth_provenance or {}
    gaps: list[str] = []
    if not case.get("trace_path"):
        gaps.append("Missing trace_path for the failed case.")
    if not case.get("artifact_paths") and not case.get("screenshots"):
        gaps.append("Missing screenshots or artifact_paths for the failed case.")
    if not case.get("failure_reason_codes"):
        gaps.append("Missing failure_reason_codes for the failed case.")
    if not case.get("console_errors") and not case.get("network_errors"):
        gaps.append("No console or network errors were captured.")
    if auth_provenance.get("session_validation_status") in {None, "", "unknown_not_captured"}:
        gaps.append("No auth/session validation status was captured.")
    if not (case.get("drift_summary") or {}).get("drift_types"):
        gaps.append("No drift classification evidence was captured.")
    return gaps[:5]


def build_stability_gate_summary(payload: dict[str, Any]) -> dict[str, Any]:
    failed_cases = payload.get("failed_cases", []) or []
    buckets = [str(case.get("stability_bucket") or "") for case in failed_cases if case.get("stability_bucket")]
    top_level_bucket = payload.get("stability_bucket")
    if not buckets and top_level_bucket:
        buckets = [str(top_level_bucket)]
    counter = Counter(buckets)
    blocking_buckets = sorted(
        bucket
        for bucket in counter
        if bucket in {"install_or_upgrade_failure", "auth_session_failure", "unknown_unclassified"}
    )
    review_buckets = sorted(
        bucket
        for bucket in counter
        if bucket
        in {
            "stale_flow_drift",
            "selector_healing_failure",
            "network_transient_infra",
            "environment_runtime_misconfig",
            "product_regression",
        }
    )
    follow_up: list[str] = []
    for bucket in blocking_buckets + review_buckets:
        recipe = _DEFAULT_RECOVERY_RECIPES.get(bucket, [])
        if recipe:
            follow_up.append(recipe[0])
    return {
        "blocking_buckets": blocking_buckets,
        "bucket_counts": dict(counter),
        "unknown_count": counter.get("unknown_unclassified", 0),
        "required_follow_up_actions": follow_up[:5],
        "release_blocked_by_stability": bool(blocking_buckets),
        "review_required_buckets": review_buckets,
    }


def build_bucket_measurement_summary(
    bucket_counts: dict[str, int],
    *,
    blocker_bucket_counts: dict[str, int] | None = None,
    total_failures: int = 0,
) -> dict[str, Any]:
    blocker_bucket_counts = blocker_bucket_counts or {}
    sorted_buckets = sorted(
        (
            {
                "bucket": bucket,
                "count": int(count),
                "rate": round(int(count) / total_failures, 4) if total_failures else None,
                "blocker_count": int(blocker_bucket_counts.get(bucket, 0)),
                "pain_score": (int(count) * 2) + (int(blocker_bucket_counts.get(bucket, 0)) * 3),
            }
            for bucket, count in bucket_counts.items()
            if bucket
        ),
        key=lambda item: (item["pain_score"], item["count"], item["blocker_count"]),
        reverse=True,
    )
    return {
        "top_bucket_counts": sorted_buckets[:5],
        "most_common_blocker_buckets": [
            {
                "bucket": bucket,
                "count": int(count),
            }
            for bucket, count in sorted(
                blocker_bucket_counts.items(),
                key=lambda item: item[1],
                reverse=True,
            )[:5]
            if bucket and count
        ],
        "highest_pain_buckets": [
            {
                "bucket": item["bucket"],
                "count": item["count"],
                "blocker_count": item["blocker_count"],
                "pain_score": item["pain_score"],
            }
            for item in sorted_buckets[:5]
        ],
        "unknown_unclassified_count": int(bucket_counts.get("unknown_unclassified", 0)),
        "unknown_unclassified_ratio": round(int(bucket_counts.get("unknown_unclassified", 0)) / total_failures, 4)
        if total_failures
        else None,
    }


def _looks_like_install_issue(
    failure_reason_codes: list[str],
    network_errors: list[str],
    console_errors: list[str],
) -> bool:
    haystack = " ".join([*failure_reason_codes, *network_errors, *console_errors])
    return any(
        token in haystack
        for token in ("playwright", "chromium", "browser", "install", "entrypoint", "executable", "module not found")
    )


def _looks_like_network_issue(
    failure_reason_codes: list[str],
    network_errors: list[str],
    console_errors: list[str],
) -> bool:
    haystack = " ".join([*failure_reason_codes, *network_errors, *console_errors])
    return any(
        token in haystack
        for token in (
            "timeout",
            "connection refused",
            "dns",
            "network",
            "502",
            "503",
            "504",
            "econn",
            "temporarily unavailable",
        )
    )
