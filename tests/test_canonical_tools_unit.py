"""Unit tests for the 4 MVP canonical tools — no browser, no real DB."""

from __future__ import annotations

import importlib
import os
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from blop.schemas import FlowStep, RecordedFlow


def _make_flow(flow_id: str = "f1", criticality: str = "revenue") -> RecordedFlow:
    return RecordedFlow(
        flow_id=flow_id,
        flow_name="checkout",
        app_url="https://example.com",
        goal="Complete checkout",
        steps=[FlowStep(step_id=0, action="navigate", value="https://example.com")],
        created_at=datetime.now(timezone.utc).isoformat(),
        business_criticality=criticality,
    )


# ---------------------------------------------------------------------------
# canonical schema contracts
# ---------------------------------------------------------------------------


def test_release_check_request_rejects_both_flow_and_journey_ids():
    from pydantic import ValidationError

    from blop.schemas import ReleaseCheckRequest

    with pytest.raises(ValidationError):
        ReleaseCheckRequest(
            app_url="https://example.com",
            flow_ids=["flow-1"],
            journey_ids=["journey-1"],
        )


def test_release_check_request_defaults_release_gating_filter():
    from blop.schemas import ReleaseCheckRequest

    request = ReleaseCheckRequest(app_url="https://example.com", criticality_filter=[])
    assert request.criticality_filter == ["revenue", "activation"]


# ---------------------------------------------------------------------------
# discover_critical_journeys
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_discover_journeys_revenue_flow_is_gated():
    """A flow with business_criticality=revenue must have include_in_release_gating=True."""
    from blop.tools.journeys import discover_critical_journeys

    mock_flows = [
        {
            "flow_name": "Checkout Flow",
            "goal": "Complete a purchase",
            "business_criticality": "revenue",
            "confidence": 0.9,
            "auth_required": False,
            "likely_assertions": ["Order confirmation shown"],
        }
    ]

    with patch("blop.engine.discovery.discover_flows", new_callable=AsyncMock, return_value={"flows": mock_flows}):
        with patch("blop.storage.sqlite.list_flows", new_callable=AsyncMock, return_value=[]):
            result = await discover_critical_journeys(app_url="https://example.com")

    assert "journeys" in result
    assert len(result["journeys"]) == 1
    assert result["journeys"][0]["include_in_release_gating"] is True


@pytest.mark.asyncio
async def test_discover_journeys_unknown_criticality_coerced_to_other():
    """Unknown business_criticality must be coerced to 'other' (not raise)."""
    from blop.tools.journeys import _flow_dict_to_critical_journey

    journey = _flow_dict_to_critical_journey(
        {"flow_name": "Some Flow", "goal": "do stuff", "business_criticality": "xyz_unknown"},
        {},
    )
    assert journey["criticality_class"] == "other"
    assert journey["include_in_release_gating"] is False


@pytest.mark.asyncio
async def test_discover_journeys_planned_ids_are_stable_and_execution_state_is_explicit():
    from blop.tools.journeys import discover_critical_journeys

    mock_flows = [
        {
            "flow_name": "Checkout Flow",
            "goal": "Complete a purchase",
            "starting_url": "https://example.com/checkout",
            "business_criticality": "revenue",
            "confidence": 0.9,
            "auth_required": False,
            "likely_assertions": ["Order confirmation shown"],
        }
    ]

    with patch("blop.engine.discovery.discover_flows", new_callable=AsyncMock, return_value={"flows": mock_flows}):
        with patch("blop.storage.sqlite.list_flows", new_callable=AsyncMock, return_value=[]):
            first = await discover_critical_journeys(app_url="https://example.com")
            second = await discover_critical_journeys(app_url="https://example.com")

    first_journey = first["journeys"][0]
    second_journey = second["journeys"][0]
    assert first_journey["journey_id"] == second_journey["journey_id"]
    assert first_journey["journey_id"].startswith("planned_journey_")
    assert first_journey["execution_status"] == "planned_only"
    assert first_journey["flow_id"] is None
    assert "gates release decisions" in first_journey["gating_reason"]


@pytest.mark.asyncio
async def test_discover_journeys_surfaces_crawl_diagnostics():
    from blop.tools.journeys import discover_critical_journeys

    mock_flows = [
        {
            "flow_name": "Checkout Flow",
            "goal": "Complete a purchase",
            "business_criticality": "revenue",
            "confidence": 0.9,
            "auth_required": False,
            "likely_assertions": ["Order confirmation shown"],
        }
    ]

    with patch(
        "blop.engine.discovery.discover_flows",
        new_callable=AsyncMock,
        return_value={
            "flows": mock_flows,
            "crawl_diagnostics": {"mode": "parallel_section_aware", "worker_count": 3},
        },
    ):
        with patch("blop.storage.sqlite.list_flows", new_callable=AsyncMock, return_value=[]):
            result = await discover_critical_journeys(app_url="https://example.com")

    assert result["crawl_diagnostics"]["mode"] == "parallel_section_aware"
    assert result["crawl_diagnostics"]["worker_count"] == 3


@pytest.mark.asyncio
async def test_journeys_resource_marks_stale_release_gating_recordings():
    from blop.tools.resources import journeys_resource

    flows = [
        {
            "flow_id": "f1",
            "flow_name": "checkout",
            "app_url": "https://example.com",
            "goal": "Complete checkout",
            "created_at": "2026-02-01T10:00:00Z",
        }
    ]
    flow_obj = _make_flow("f1", "revenue")

    with patch.dict(os.environ, {"BLOP_FLOW_STALE_DAYS": "14"}):
        with patch("blop.storage.sqlite.list_flows", new_callable=AsyncMock, return_value=flows):
            with patch("blop.storage.sqlite.get_flow", new_callable=AsyncMock, return_value=flow_obj):
                with patch("blop.storage.sqlite.get_latest_context_graph", new_callable=AsyncMock, return_value=None):
                    result = await journeys_resource()

    assert result["stale_release_gating_count"] == 1
    assert result["journeys"][0]["stale_recording"] is True
    assert "record_test_flow" in result["journeys"][0]["recommended_next_action"]
    assert "stale" in result["workflow_hint"].lower()


# ---------------------------------------------------------------------------
# run_release_check
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_run_release_check_no_flows_returns_structured_error():
    """replay mode with no matching flows → error dict with release_id."""
    from blop.tools.release_check import run_release_check

    with patch("blop.storage.sqlite.list_flows_full", new_callable=AsyncMock, return_value=[]):
        result = await run_release_check(
            app_url="https://example.com",
            mode="replay",
            release_id="rel-test-001",
        )

    assert "error" in result
    assert result.get("release_id") == "rel-test-001"


@pytest.mark.asyncio
async def test_run_release_check_result_contains_resource_links():
    """replay mode with flows → result contains resource_links."""
    from blop.tools.release_check import run_release_check

    mock_run_result = {"run_id": "run-abc", "status": "queued"}
    selected_flow = _make_flow("f1", "revenue")

    with patch("blop.storage.sqlite.list_flows_full", new_callable=AsyncMock, return_value=[selected_flow]):
        with patch("blop.tools.regression.run_regression_test", new_callable=AsyncMock, return_value=mock_run_result):
            with patch("blop.storage.sqlite.save_release_brief", new_callable=AsyncMock):
                result = await run_release_check(
                    app_url="https://example.com",
                    mode="replay",
                    release_id="rel-002",
                )

    assert "resource_links" in result
    assert "brief" in result["resource_links"]
    assert "risk" in result
    assert "confidence" in result
    assert result["decision"] == "INVESTIGATE"
    assert isinstance(result["prioritized_actions"], list)
    assert "selected_flows" in result
    assert "execution_plan_summary" in result
    assert result["selected_flows"][0]["flow_id"] == "f1"
    assert result["active_gating_policy"]["criticality_filter"] == ["revenue", "activation"]
    assert result["recommended_next_step"]["tool"] == "get_test_results"
    assert "stability_gate_summary" in result
    assert "release_exit_criteria" in result


@pytest.mark.asyncio
async def test_run_release_check_targeted_uses_expanded_budget_and_stored_report():
    """targeted mode should pass a larger step budget and build its result from get_test_results."""
    from blop.tools.release_check import run_release_check

    eval_result = {
        "run_id": "run-targeted-1",
        "summary": ["Task encountered issues"],
        "release_recommendation": {
            "decision": "BLOCK",
            "confidence": "medium",
            "rationale": "Task failed with explicit error or missing resource. Investigate before shipping.",
        },
    }
    stored_report = {
        "run_id": "run-targeted-1",
        "status": "completed",
        "release_recommendation": {
            "decision": "BLOCK",
            "confidence": "medium",
            "rationale": "1 blocker(s) and 0 critical journey failure(s) detected. Do not ship until these are resolved.",
        },
        "severity_counts": {"blocker": 1},
        "cases": [{"flow_name": "targeted_evaluation", "status": "fail", "severity": "blocker"}],
        "failed_cases": [{"flow_name": "targeted_evaluation", "status": "fail", "severity": "blocker"}],
        "next_actions": ["Investigate before shipping."],
        "top_failure_mode": "automation_fragility",
        "stability_bucket": "unknown_unclassified",
        "recommended_remediation_steps": ["Inspect traces", "Refresh flow", "Re-run"],
        "decision_summary": {"decision": "BLOCK", "next_recommended_action": "Inspect traces"},
        "evidence_summary": {"failed_case_count": 1, "top_artifact_refs": []},
        "coverage_summary": {"failure_kinds": ["automation_fragility"]},
        "evidence_quality": {"confidence_drivers": ["Evidence capture is sparse"]},
        "drift_summary": {"drift_detected": False, "plan_fidelity": "low"},
        "replay_trust_summary": {"golden_path_ready": True, "review_required_case_count": 0},
        "failure_classification": {"primary": "automation_fragility", "confidence": "high"},
        "failure_links": {
            "artifacts": [],
            "resources": [],
            "triage_hint": "triage_release_blocker(run_id='run-targeted-1')",
        },
        "bucket_confidence": "low",
        "unknown_classification_gaps": ["Missing trace_path for the failed case."],
        "recommended_next_action": "Inspect traces",
        "workflow_hint": "Inspect traces",
        "is_terminal": True,
    }

    with patch.dict(os.environ, {"BLOP_TARGETED_MAX_STEPS": "41"}):
        with patch(
            "blop.tools.evaluate.evaluate_web_task", new_callable=AsyncMock, return_value=eval_result
        ) as eval_mock:
            with patch("blop.tools.results.get_test_results", new_callable=AsyncMock, return_value=stored_report):
                with patch("blop.storage.sqlite.save_release_brief", new_callable=AsyncMock):
                    result = await run_release_check(
                        app_url="https://example.com",
                        mode="targeted",
                        release_id="rel-targeted-1",
                    )

    eval_mock.assert_awaited_once()
    assert eval_mock.await_args.kwargs["max_steps"] == 41
    assert result["release_id"] == "rel-targeted-1"
    assert result["run_id"] == "run-targeted-1"
    assert result["decision"] == "BLOCK"
    assert result["evaluation_summary"] == ["Task encountered issues"]
    assert result["failure_classification"]["primary"] == "automation_fragility"
    assert result["recommended_next_step"]["tool"] == "triage_release_blocker"
    assert result["stability_gate_summary"]["unknown_count"] == 1
    assert result["release_exit_criteria"]["release_blocked_by_stability"] is True


@pytest.mark.asyncio
async def test_release_readiness_prompt_defines_replay_golden_path():
    """The release prompt should define record -> replay as the canonical workflow."""
    from blop.tools.prompts import RELEASE_READINESS_REVIEW

    assert "record_test_flow" in RELEASE_READINESS_REVIEW
    assert 'run_release_check(app_url="https://your-app.com", mode="replay")' in RELEASE_READINESS_REVIEW
    assert "golden path" in RELEASE_READINESS_REVIEW.lower()


# ---------------------------------------------------------------------------
# triage_release_blocker
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_triage_resolves_run_id_from_release_id():
    """When only release_id is given, triage must look up the run_id."""
    from blop.tools.triage import triage_release_blocker

    brief = {"run_id": "run-from-release", "app_url": "https://example.com"}
    with patch("blop.storage.sqlite.get_release_brief", new_callable=AsyncMock, return_value=brief):
        with patch("blop.storage.sqlite.get_run", new_callable=AsyncMock, return_value={"run_id": "run-from-release"}):
            with patch("blop.storage.sqlite.list_cases_for_run", new_callable=AsyncMock, return_value=[]):
                with patch("blop.storage.sqlite.list_artifacts_for_run", new_callable=AsyncMock, return_value=[]):
                    result = await triage_release_blocker(release_id="rel-abc")

    # subject_id should be the resolved run_id
    assert result.get("subject_id") == "run-from-release"


@pytest.mark.asyncio
async def test_triage_revenue_failure_mentions_revenue_in_impact():
    """A failed case with business_criticality=revenue → user_business_impact mentions Revenue-critical."""
    from blop.schemas import FailureCase
    from blop.tools.triage import triage_release_blocker

    case = FailureCase(
        run_id="r1",
        flow_id="f1",
        flow_name="checkout",
        status="fail",
        severity="blocker",
        business_criticality="revenue",
    )

    with patch("blop.storage.sqlite.get_run", new_callable=AsyncMock, return_value={"run_id": "r1"}):
        with patch("blop.storage.sqlite.list_cases_for_run", new_callable=AsyncMock, return_value=[case]):
            with patch("blop.storage.sqlite.list_artifacts_for_run", new_callable=AsyncMock, return_value=[]):
                result = await triage_release_blocker(run_id="r1")

    assert (
        "Revenue" in result.get("user_business_impact", "")
        or "revenue" in result.get("user_business_impact", "").lower()
    )
    assert result["subject_type"] == "run"
    assert result["business_priority"] == "release_blocker"
    assert "confidence" in result["confidence_note"].lower()
    assert "top_evidence_refs" in result["evidence_summary_compact"]


# ---------------------------------------------------------------------------
# validate_release_setup
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_validate_release_setup_suggested_steps_canonical_names():
    """suggested_next_steps must reference discover_critical_journeys, not discover_test_flows."""
    from blop.tools.validate import validate_release_setup

    mock_browser = AsyncMock()
    mock_playwright = AsyncMock()
    mock_playwright.__aenter__ = AsyncMock(return_value=mock_playwright)
    mock_playwright.__aexit__ = AsyncMock(return_value=False)
    mock_playwright.chromium.launch.return_value = mock_browser
    mock_resp = MagicMock()
    mock_resp.status = 200
    mock_urlopen_ctx = MagicMock()
    mock_urlopen_ctx.__enter__ = MagicMock(return_value=mock_resp)
    mock_urlopen_ctx.__exit__ = MagicMock(return_value=False)

    with patch.dict(os.environ, {"GOOGLE_API_KEY": "k"}):
        with patch("playwright.async_api.async_playwright", return_value=mock_playwright):
            with patch("blop.storage.sqlite.init_db", new_callable=AsyncMock):
                with patch("urllib.request.urlopen", return_value=mock_urlopen_ctx):
                    result = await validate_release_setup(app_url="https://example.com")

    steps = " ".join(result.get("suggested_next_steps", []))
    assert "discover_critical_journeys" in steps
    assert "discover_test_flows" not in steps
    assert "blop://journeys" in steps
    assert "triage_release_blocker" in steps


# ---------------------------------------------------------------------------
# compat wrappers
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_compat_validate_setup_adds_deprecation_metadata(monkeypatch):
    import blop.tools.compat as compat

    compat = importlib.reload(compat)
    monkeypatch.setattr(compat, "BLOP_ENABLE_COMPAT_TOOLS", True)

    with patch("blop.tools.validate.validate_release_setup", new_callable=AsyncMock, return_value={"status": "ready"}):
        result = await compat.validate_setup(app_url="https://example.com")

    assert result["deprecated"] is True
    assert result["deprecation_notice"]["replacement_tool"] == "validate_release_setup"


@pytest.mark.asyncio
async def test_compat_list_recorded_tests_delegates_to_journeys_resource(monkeypatch):
    import blop.tools.compat as compat

    compat = importlib.reload(compat)
    monkeypatch.setattr(compat, "BLOP_ENABLE_COMPAT_TOOLS", True)

    with patch(
        "blop.tools.resources.journeys_resource", new_callable=AsyncMock, return_value={"journeys": [], "total": 0}
    ):
        result = await compat.list_recorded_tests()

    assert result["deprecated"] is True
    assert result["deprecation_notice"]["replacement_tool"] == "blop://journeys"
    assert result["total"] == 0
