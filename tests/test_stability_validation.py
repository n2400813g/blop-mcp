from __future__ import annotations

from blop.stability import (
    build_stability_gate_summary,
    classify_case_stability,
    classify_validation_issue,
)


def test_replay_smoke_stale_recorded_flow_maps_to_stale_flow_drift():
    result = classify_case_stability(
        {
            "status": "fail",
            "flow_staleness": {"stale": True},
            "failure_reason_codes": ["selector_not_found"],
        }
    )

    assert result["stability_bucket"] == "stale_flow_drift"


def test_replay_smoke_expired_auth_maps_to_auth_session_failure():
    result = classify_case_stability(
        {
            "status": "blocked",
            "failure_reason_codes": ["auth_required"],
        },
        auth_provenance={"session_validation_status": "expired_session"},
    )

    assert result["stability_bucket"] == "auth_session_failure"


def test_replay_smoke_repair_rejected_maps_to_selector_healing_failure():
    result = classify_case_stability(
        {
            "status": "fail",
            "failure_reason_codes": ["repair_rejected"],
            "healing_decision": "propose_patch",
        }
    )

    assert result["stability_bucket"] == "selector_healing_failure"


def test_replay_smoke_environment_mismatch_maps_to_runtime_misconfig():
    result = classify_case_stability(
        {
            "status": "error",
            "failure_class": "env_issue",
            "failure_reason_codes": ["storage_state_path_mismatch"],
            "console_errors": ["missing runtime dependency"],
        }
    )

    assert result["stability_bucket"] == "environment_runtime_misconfig"


def test_install_smoke_chromium_bootstrap_failure_maps_to_install_bucket():
    result = classify_validation_issue(
        "chromium_installed",
        "Chromium executable not found during clean install smoke",
        passed=False,
    )

    assert result["stability_bucket"] == "install_or_upgrade_failure"


def test_install_smoke_repair_case_runtime_breakage_maps_to_runtime_bucket():
    result = classify_validation_issue(
        "runtime_config",
        "Broken BLOP_RUNS_DIR path blocked runtime repair flow",
        passed=False,
    )

    assert result["stability_bucket"] == "environment_runtime_misconfig"


def test_install_smoke_upgrade_registry_timeout_maps_to_network_bucket():
    result = classify_validation_issue(
        "app_url_reachable",
        "Package upgrade timed out because registry network was not reachable",
        passed=False,
    )

    assert result["stability_bucket"] == "network_transient_infra"


def test_release_gate_summary_blocks_unknown_and_install_failures_only():
    summary = build_stability_gate_summary(
        {
            "failed_cases": [
                {"stability_bucket": "install_or_upgrade_failure"},
                {"stability_bucket": "unknown_unclassified"},
                {"stability_bucket": "selector_healing_failure"},
            ]
        }
    )

    assert summary["blocking_buckets"] == ["install_or_upgrade_failure", "unknown_unclassified"]
    assert summary["review_required_buckets"] == ["selector_healing_failure"]
    assert summary["release_blocked_by_stability"] is True
