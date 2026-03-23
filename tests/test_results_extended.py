"""Tests for get_run_health_stream and get_risk_analytics from blop.tools.results."""
from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from blop.schemas import AuthProfile, DriftSummary, FailureCase, IntentContract
from blop.tools import results


@pytest.mark.asyncio
async def test_health_stream_happy_path():
    """get_run_health_stream returns run status and events when run exists."""
    run = {
        "run_id": "run1",
        "status": "running",
        "app_url": "https://example.com",
    }
    events = [
        {"event_id": "e1", "run_id": "run1", "event_type": "started", "payload": {}, "created_at": "2026-03-19T10:00:00Z"},
        {"event_id": "e2", "run_id": "run1", "event_type": "case_complete", "payload": {"case_id": "c1"}, "created_at": "2026-03-19T10:01:00Z"},
    ]
    with patch("blop.storage.sqlite.get_run", new_callable=AsyncMock, return_value=run):
        with patch("blop.storage.sqlite.list_run_health_events", new_callable=AsyncMock, return_value=events):
            out = await results.get_run_health_stream("run1", limit=500)
    assert out["run_id"] == "run1"
    assert out["status"] == "running"
    assert out["event_count"] == 2
    assert len(out["events"]) == 2
    assert out["events"][0]["event_type"] == "started"


@pytest.mark.asyncio
async def test_health_stream_not_found():
    """get_run_health_stream returns error when run does not exist."""
    with patch("blop.storage.sqlite.get_run", new_callable=AsyncMock, return_value=None):
        out = await results.get_run_health_stream("nonexistent", limit=500)
    assert "error" in out
    assert "not found" in out["error"]


@pytest.mark.asyncio
async def test_risk_analytics_with_cases():
    """get_risk_analytics aggregates business_risk from cases with various criticality and statuses."""
    runs = [
        {"run_id": "run1"},
        {"run_id": "run2"},
    ]
    case1 = FailureCase(
        run_id="run1",
        flow_id="f1",
        flow_name="checkout",
        status="fail",
        business_criticality="revenue",
        step_failure_index=2,
    )
    case2 = FailureCase(
        run_id="run1",
        flow_id="f2",
        flow_name="signup",
        status="pass",
        business_criticality="activation",
    )
    case3 = FailureCase(
        run_id="run2",
        flow_id="f3",
        flow_name="billing",
        status="error",
        failure_class="env_issue",
        business_criticality="revenue",
        step_failure_index=1,
    )
    async def list_cases_side_effect(run_id: str):
        if run_id == "run1":
            return [case1, case2]
        return [case3]

    with patch("blop.storage.sqlite.list_runs", new_callable=AsyncMock, return_value=runs):
        with patch(
            "blop.storage.sqlite.list_cases_for_run",
            new_callable=AsyncMock,
            side_effect=list_cases_side_effect,
        ):
            with patch("blop.storage.sqlite.get_flow", new_callable=AsyncMock, return_value=None):
                with patch("blop.storage.sqlite.list_telemetry_signals", new_callable=AsyncMock, return_value=[]):
                    out = await results.get_risk_analytics(limit_runs=30)

    assert out["analyzed_runs"] == 2
    assert out["business_risk"]["revenue"]["total"] == 2
    assert out["business_risk"]["revenue"]["failed"] == 2
    assert out["business_risk"]["activation"]["total"] == 1
    assert out["business_risk"]["activation"]["failed"] == 0
    assert "flaky_steps_leaderboard" in out
    assert "failing_transitions" in out
    assert out["stability_buckets"]["unknown_unclassified"]["count"] == 1
    assert out["stability_buckets"]["environment_runtime_misconfig"]["count"] == 1


@pytest.mark.asyncio
async def test_risk_analytics_empty():
    """get_risk_analytics returns analyzed_runs=0 when no runs exist."""
    with patch("blop.storage.sqlite.list_runs", new_callable=AsyncMock, return_value=[]):
        out = await results.get_risk_analytics(limit_runs=30)
    assert out["analyzed_runs"] == 0
    assert out["business_risk"]["revenue"]["total"] == 0
    assert out["business_risk"]["revenue"]["failed"] == 0


@pytest.mark.asyncio
async def test_risk_analytics_includes_internal_validation_signals():
    runs = [{"run_id": "run1", "app_url": "https://example.com"}]

    with patch("blop.storage.sqlite.list_runs", new_callable=AsyncMock, return_value=runs):
        with patch("blop.storage.sqlite.list_cases_for_run", new_callable=AsyncMock, return_value=[]):
            with patch("blop.storage.sqlite.list_risk_calibration", new_callable=AsyncMock, return_value=[]):
                with patch(
                    "blop.storage.sqlite.list_telemetry_signals",
                    new_callable=AsyncMock,
                    return_value=[
                        {
                            "signal_id": "sig-1",
                            "app_url": "https://example.com",
                            "source": "custom",
                            "ts": "2026-03-19T10:00:00Z",
                            "signal_type": "custom",
                            "journey_key": None,
                            "route": None,
                            "value": 1.0,
                            "unit": "count",
                            "tags": {
                                "surface": "validate",
                                "bucket": "network_transient_infra",
                                "status": "warning",
                                "reason_code": "app_url_reachable",
                            },
                        }
                    ],
                ):
                    out = await results.get_risk_analytics(limit_runs=30)

    assert out["stability_buckets"]["network_transient_infra"]["count"] == 1
    assert out["surface_bucket_breakdown"]["validate"]["network_transient_infra"] == 1


@pytest.mark.asyncio
async def test_get_test_results_adds_summary_first_fields():
    run = {
        "run_id": "run-summary",
        "status": "completed",
        "started_at": "2026-03-19T10:00:00Z",
        "completed_at": "2026-03-19T10:02:00Z",
        "artifacts_dir": "/tmp/runs/run-summary",
        "run_mode": "hybrid",
        "app_url": "https://example.com",
        "next_actions": ["Fix checkout CTA visibility"],
        "profile_name": "auth-profile",
        "headless": True,
    }
    cases = [
        FailureCase(
            case_id="case-1",
            run_id="run-summary",
            flow_id="flow-1",
            flow_name="checkout",
            status="fail",
            severity="blocker",
            business_criticality="revenue",
            console_errors=["TypeError: x is undefined"],
            network_errors=["500 https://example.com/api/checkout"],
            screenshots=["/tmp/runs/run-summary/screenshots/1.png"],
            trace_path="/tmp/runs/run-summary/traces/case-1.zip",
            failure_class="test_fragility",
            healing_decision="propose_patch",
            repair_confidence=0.62,
        )
    ]
    auth_profile = AuthProfile(
        profile_name="auth-profile",
        auth_type="storage_state",
        storage_state_path="/tmp/auth_state.json",
    )
    events = [
        {
            "event_id": "evt-auth",
            "run_id": "run-summary",
            "event_type": "auth_context_resolved",
            "payload": {
                "profile_name": "auth-profile",
                "auth_used": True,
                "auth_source": "storage_state",
                "storage_state_path": "/tmp/auth_state.json",
                "session_validation_status": "valid",
            },
            "created_at": "2026-03-19T10:00:01Z",
        },
        {
            "event_id": "evt-land",
            "run_id": "run-summary",
            "event_type": "auth_landing_observed",
            "payload": {
                "flow_id": "flow-1",
                "flow_name": "checkout",
                "expected_url": "https://example.com/checkout",
                "landed_url": "https://example.com/checkout",
                "page_title": "Checkout",
                "landed_authenticated": True,
            },
            "created_at": "2026-03-19T10:00:02Z",
        },
    ]

    with patch("blop.storage.sqlite.get_run", new_callable=AsyncMock, return_value=run):
        with patch("blop.storage.sqlite.list_cases_for_run", new_callable=AsyncMock, return_value=cases):
            with patch("blop.storage.sqlite.list_run_health_events", new_callable=AsyncMock, return_value=events):
                with patch("blop.storage.sqlite.get_auth_profile", new_callable=AsyncMock, return_value=auth_profile):
                    with patch("blop.storage.sqlite.get_flow", new_callable=AsyncMock, return_value=None):
                        out = await results.get_test_results("run-summary")

    assert out["decision_summary"]["decision"] == "BLOCK"
    assert out["decision_summary"]["top_blocker_journeys"] == ["checkout"]
    assert out["evidence_summary"]["failed_case_count"] == 1
    assert out["evidence_summary"]["top_artifact_refs"] == [
        "/tmp/runs/run-summary/screenshots/1.png",
        "/tmp/runs/run-summary/traces/case-1.zip",
    ]
    assert out["evidence_quality"]["traces_present"] is True
    assert "automation fragility" in out["evidence_quality"]["confidence_drivers"][-1].lower()
    assert out["coverage_summary"]["failure_kinds"] == ["automation_fragility"]
    assert out["auth_provenance"]["auth_source"] == "storage_state"
    assert out["auth_provenance"]["session_validation_status"] == "valid"
    assert out["auth_provenance"]["landed_authenticated"] is True
    assert out["auth_provenance"]["landing_url"] == "https://example.com/checkout"
    assert out["run_environment"]["headless"] is True
    assert out["top_failure_mode"] == "automation_fragility"
    assert len(out["recommended_remediation_steps"]) == 3
    assert out["is_terminal"] is True
    assert out["failure_classification"]["primary"] == "automation_fragility"
    assert out["failure_links"]["artifacts"] == [
        "/tmp/runs/run-summary/screenshots/1.png",
        "/tmp/runs/run-summary/traces/case-1.zip",
    ]
    assert out["failure_links"]["triage_hint"] == "triage_release_blocker(run_id='run-summary')"
    assert out["replay_trust_summary"]["review_required_case_count"] == 1
    assert out["replay_trust_summary"]["golden_path_ready"] is False
    assert out["stability_bucket"] == "selector_healing_failure"
    assert out["bucket_confidence"] == "high"
    assert out["bucket_next_action"] == "Inspect the failed step and repair evidence."
    assert out["failed_cases"][0]["stability_bucket"] == "selector_healing_failure"
    assert out["stability_gate_summary"]["review_required_buckets"] == ["selector_healing_failure"]
    assert out["stability_measurement"]["top_bucket_counts"][0]["bucket"] == "selector_healing_failure"
    assert out["stability_measurement"]["most_common_blocker_buckets"][0]["bucket"] == "selector_healing_failure"
    assert "cases" in out
    assert "failed_cases" in out


@pytest.mark.asyncio
async def test_get_test_results_stale_flow_guidance_prioritizes_refresh():
    run = {
        "run_id": "run-stale",
        "status": "failed",
        "started_at": "2026-03-19T10:00:00Z",
        "completed_at": "2026-03-19T10:02:00Z",
        "artifacts_dir": "/tmp/runs/run-stale",
        "run_mode": "hybrid",
        "app_url": "https://example.com",
        "next_actions": ["Inspect top failed case"],
        "profile_name": None,
        "headless": True,
    }
    cases = [
        FailureCase(
            case_id="case-stale-1",
            run_id="run-stale",
            flow_id="flow-stale",
            flow_name="checkout",
            status="fail",
            severity="high",
            business_criticality="revenue",
            screenshots=["/tmp/runs/run-stale/screenshots/1.png"],
            trace_path="/tmp/runs/run-stale/traces/case-stale-1.zip",
            failure_class="test_fragility",
        )
    ]
    events: list[dict] = []
    stale_flow = type("FlowStub", (), {"created_at": "2026-02-20T10:00:00Z"})()

    with patch("blop.storage.sqlite.get_run", new_callable=AsyncMock, return_value=run):
        with patch("blop.storage.sqlite.list_cases_for_run", new_callable=AsyncMock, return_value=cases):
            with patch("blop.storage.sqlite.list_run_health_events", new_callable=AsyncMock, return_value=events):
                with patch("blop.storage.sqlite.get_auth_profile", new_callable=AsyncMock, return_value=None):
                    with patch.dict("os.environ", {"BLOP_FLOW_STALE_DAYS": "14"}):
                        with patch("blop.storage.sqlite.get_flow", new_callable=AsyncMock, return_value=stale_flow):
                            out = await results.get_test_results("run-stale")

    assert out["stale_flow_guidance"].startswith("Refresh the stale recording")
    assert out["recommended_next_action"] == out["stale_flow_guidance"]
    assert out["workflow_hint"] == out["stale_flow_guidance"]
    assert out["stability_bucket"] == "stale_flow_drift"
    assert out["failed_cases"][0]["stability_bucket"] == "stale_flow_drift"


@pytest.mark.asyncio
async def test_get_test_results_success_report_includes_coverage_proof():
    run = {
        "run_id": "run-pass",
        "status": "completed",
        "started_at": "2026-03-19T10:00:00Z",
        "completed_at": "2026-03-19T10:02:00Z",
        "artifacts_dir": "/tmp/runs/run-pass",
        "run_mode": "strict_steps",
        "app_url": "https://app.example.com/editor",
        "next_actions": [],
        "profile_name": "manual-auth",
        "headless": False,
    }
    cases = [
        FailureCase(
            case_id="case-pass-1",
            run_id="run-pass",
            flow_id="flow-1",
            flow_name="editor_text_panel",
            status="pass",
            severity="none",
            screenshots=["/tmp/runs/run-pass/screenshots/text.png"],
            trace_path="/tmp/runs/run-pass/traces/text.zip",
            assertion_results=[
                {"assertion": "Add Heading", "passed": True, "eval_type": "text_present"},
            ],
        ),
        FailureCase(
            case_id="case-pass-2",
            run_id="run-pass",
            flow_id="flow-2",
            flow_name="editor_captions_panel",
            status="pass",
            severity="none",
            screenshots=["/tmp/runs/run-pass/screenshots/captions.png"],
            trace_path="/tmp/runs/run-pass/traces/captions.zip",
            assertion_results=[
                {"assertion": "Auto captions", "passed": True, "eval_type": "text_present"},
            ],
        ),
        FailureCase(
            case_id="case-pass-3",
            run_id="run-pass",
            flow_id="flow-3",
            flow_name="editor_ai_agent_panel",
            status="pass",
            severity="none",
            screenshots=["/tmp/runs/run-pass/screenshots/ai-agent.png"],
            trace_path="/tmp/runs/run-pass/traces/ai-agent.zip",
            assertion_results=[
                {
                    "assertion": "Create an animated intro for my brand",
                    "passed": True,
                    "eval_type": "text_present",
                },
            ],
        ),
    ]
    auth_profile = AuthProfile(
        profile_name="manual-auth",
        auth_type="storage_state",
        storage_state_path="/tmp/manual-auth.json",
        user_data_dir="/tmp/manual-auth-dir",
    )
    events = [
        {
            "event_id": "evt-auth-pass",
            "run_id": "run-pass",
            "event_type": "auth_context_resolved",
            "payload": {
                "profile_name": "manual-auth",
                "auth_used": True,
                "auth_source": "storage_state",
                "storage_state_path": "/tmp/manual-auth.json",
                "user_data_dir": "/tmp/manual-auth-dir",
                "session_validation_status": "valid",
            },
            "created_at": "2026-03-19T10:00:01Z",
        },
        {
            "event_id": "evt-land-pass",
            "run_id": "run-pass",
            "event_type": "auth_landing_observed",
            "payload": {
                "flow_id": "flow-1",
                "flow_name": "editor_text_panel",
                "expected_url": "https://app.example.com/editor",
                "landed_url": "https://app.example.com/editor/123",
                "page_title": "Untitled Project",
                "landed_authenticated": True,
            },
            "created_at": "2026-03-19T10:00:02Z",
        },
    ]

    with patch("blop.storage.sqlite.get_run", new_callable=AsyncMock, return_value=run):
        with patch("blop.storage.sqlite.list_cases_for_run", new_callable=AsyncMock, return_value=cases):
            with patch("blop.storage.sqlite.list_run_health_events", new_callable=AsyncMock, return_value=events):
                with patch("blop.storage.sqlite.get_auth_profile", new_callable=AsyncMock, return_value=auth_profile):
                    with patch("blop.storage.sqlite.get_flow", new_callable=AsyncMock, return_value=None):
                        out = await results.get_test_results("run-pass")

    assert out["decision_summary"]["decision"] == "SHIP"
    assert out["decision_summary"]["verified_journeys"] == [
        "editor_text_panel",
        "editor_captions_panel",
        "editor_ai_agent_panel",
    ]
    assert out["coverage_summary"]["observed_assertions"] == [
        "Add Heading",
        "Auto captions",
        "Create an animated intro for my brand",
    ]
    assert out["coverage_summary"]["proof_artifact_refs"][0] == "/tmp/runs/run-pass/screenshots/text.png"
    assert out["evidence_summary"]["trace_count"] == 3
    assert out["evidence_quality"]["screenshots_present"] is True
    assert out["evidence_quality"]["traces_present"] is True
    assert out["auth_provenance"]["profile_name"] == "manual-auth"
    assert out["auth_provenance"]["user_data_dir"] == "/tmp/manual-auth-dir"
    assert out["auth_provenance"]["landing_page_title"] == "Untitled Project"
    assert out["auth_provenance"]["landed_authenticated"] is True
    assert out["run_environment"]["headless"] is False
    assert out["top_failure_mode"] == "unknown"
    assert out["stability_bucket"] is None


@pytest.mark.asyncio
async def test_get_test_results_downgrades_drifted_critical_passes():
    run = {
        "run_id": "run-drift",
        "status": "completed",
        "started_at": "2026-03-19T10:00:00Z",
        "completed_at": "2026-03-19T10:02:00Z",
        "artifacts_dir": "/tmp/runs/run-drift",
        "run_mode": "hybrid",
        "app_url": "https://example.com",
        "next_actions": [],
        "profile_name": None,
        "headless": True,
    }
    cases = [
        FailureCase(
            case_id="case-drift-1",
            run_id="run-drift",
            flow_id="flow-1",
            flow_name="checkout",
            status="pass",
            severity="none",
            business_criticality="revenue",
            intent_contract=IntentContract(
                goal_text="Complete checkout",
                goal_type="transaction",
                target_surface="authenticated_app",
                success_assertions=["Order confirmation shown"],
                must_interact=["navigate", "click_primary"],
                forbidden_shortcuts=["goal_fallback_without_surface_match"],
                scope="authed",
                business_criticality="revenue",
                planning_source="explicit_goal",
                expected_url_patterns=["/checkout"],
                allowed_fallbacks=["hybrid_repair"],
            ),
            drift_summary=DriftSummary(
                drift_detected=True,
                drift_types=["plan_drift"],
                disallowed_fallback_used=["goal_fallback"],
                surface_match=True,
                assertion_match=True,
                plan_fidelity="low",
            ),
        )
    ]

    with patch("blop.storage.sqlite.get_run", new_callable=AsyncMock, return_value=run):
        with patch("blop.storage.sqlite.list_cases_for_run", new_callable=AsyncMock, return_value=cases):
            with patch("blop.storage.sqlite.list_run_health_events", new_callable=AsyncMock, return_value=[]):
                with patch("blop.storage.sqlite.get_flow", new_callable=AsyncMock, return_value=None):
                    out = await results.get_test_results("run-drift")

    assert out["decision_summary"]["decision"] == "INVESTIGATE"
    assert out["decision_summary"]["critical_drifted_passes"] == 1


@pytest.mark.asyncio
async def test_get_test_results_waiting_auth_uses_auth_status_guidance():
    run = {
        "run_id": "run-auth",
        "status": "waiting_auth",
        "started_at": "2026-03-19T10:00:00Z",
        "completed_at": None,
        "artifacts_dir": "/tmp/runs/run-auth",
        "run_mode": "hybrid",
        "app_url": "https://example.com",
        "next_actions": [],
        "profile_name": "prod",
        "headless": True,
    }
    events = [
        {
            "event_id": "evt-auth",
            "run_id": "run-auth",
            "event_type": "auth_context_resolved",
            "payload": {
                "profile_name": "prod",
                "auth_used": True,
                "auth_source": "storage_state",
                "storage_state_path": "/tmp/prod.json",
                "session_validation_status": "expired_session",
            },
            "created_at": "2026-03-19T10:00:01Z",
        }
    ]

    with patch("blop.storage.sqlite.get_run", new_callable=AsyncMock, return_value=run):
        with patch("blop.storage.sqlite.list_cases_for_run", new_callable=AsyncMock, return_value=[]):
            with patch("blop.storage.sqlite.list_run_health_events", new_callable=AsyncMock, return_value=events):
                with patch("blop.storage.sqlite.get_auth_profile", new_callable=AsyncMock, return_value=None):
                    with patch("blop.storage.sqlite.get_flow", new_callable=AsyncMock, return_value=None):
                        out = await results.get_test_results("run-auth")

    assert out["status"] == "waiting_auth"
    assert out["top_failure_mode"] == "waiting_auth"
    assert "expired session" in out["waiting_auth_message"].lower()
    assert out["is_terminal"] is True
    assert "capture_auth_session" in " ".join(out["recommended_remediation_steps"])
    assert out["drift_summary"]["drift_detected"] is True
    assert out["drift_summary"]["plan_fidelity"] == "low"
    assert out["stability_bucket"] == "auth_session_failure"
    assert out["bucket_confidence"] == "high"
    assert out["stability_gate_summary"]["blocking_buckets"] == ["auth_session_failure"]


@pytest.mark.asyncio
async def test_get_test_results_unknown_bucket_includes_classification_gaps():
    run = {
        "run_id": "run-unknown",
        "status": "failed",
        "started_at": "2026-03-19T10:00:00Z",
        "completed_at": "2026-03-19T10:02:00Z",
        "artifacts_dir": "/tmp/runs/run-unknown",
        "run_mode": "hybrid",
        "app_url": "https://example.com",
        "next_actions": [],
        "profile_name": None,
        "headless": True,
    }
    cases = [
        FailureCase(
            case_id="case-unknown-1",
            run_id="run-unknown",
            flow_id="flow-unknown",
            flow_name="checkout",
            status="fail",
            severity="high",
            business_criticality="revenue",
        )
    ]

    with patch("blop.storage.sqlite.get_run", new_callable=AsyncMock, return_value=run):
        with patch("blop.storage.sqlite.list_cases_for_run", new_callable=AsyncMock, return_value=cases):
            with patch("blop.storage.sqlite.list_run_health_events", new_callable=AsyncMock, return_value=[]):
                with patch("blop.storage.sqlite.get_auth_profile", new_callable=AsyncMock, return_value=None):
                    with patch("blop.storage.sqlite.get_flow", new_callable=AsyncMock, return_value=None):
                        out = await results.get_test_results("run-unknown")

    assert out["stability_bucket"] == "unknown_unclassified"
    assert out["bucket_confidence"] == "low"
    assert out["unknown_classification_gaps"]
    assert out["unknown_next_observation"] == out["unknown_classification_gaps"][0]
    assert out["failed_cases"][0]["unknown_classification_gaps"]


@pytest.mark.asyncio
async def test_risk_analytics_returns_bucket_measurement_summary():
    runs = [{"run_id": "run-1", "app_url": "https://example.com"}]
    cases = [
        FailureCase(
            run_id="run-1",
            flow_id="flow-auth",
            flow_name="checkout",
            status="fail",
            severity="blocker",
            failure_class="auth_failure",
            business_criticality="revenue",
        ),
        FailureCase(
            run_id="run-1",
            flow_id="flow-drift",
            flow_name="settings",
            status="fail",
            severity="high",
            failure_class="test_fragility",
            healing_decision="propose_patch",
            business_criticality="support",
        ),
    ]

    with patch("blop.storage.sqlite.list_runs", new_callable=AsyncMock, return_value=runs):
        with patch("blop.storage.sqlite.list_cases_for_run", new_callable=AsyncMock, return_value=cases):
            with patch("blop.storage.sqlite.get_flow", new_callable=AsyncMock, return_value=None):
                with patch("blop.storage.sqlite.list_telemetry_signals", new_callable=AsyncMock, return_value=[]):
                    with patch("blop.storage.sqlite.list_risk_calibration", new_callable=AsyncMock, return_value=[]):
                        out = await results.get_risk_analytics(limit_runs=30)

    assert out["stability_measurement"]["top_bucket_counts"][0]["bucket"] == "auth_session_failure"
    assert out["stability_measurement"]["most_common_blocker_buckets"][0]["bucket"] == "auth_session_failure"
    assert out["stability_measurement"]["highest_pain_buckets"][0]["pain_score"] >= 1
