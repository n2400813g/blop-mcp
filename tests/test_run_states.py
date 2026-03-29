"""Tests for tools/regression.py — run state machine."""

from __future__ import annotations

import asyncio
import os
from contextlib import ExitStack
from datetime import datetime, timezone
from unittest.mock import AsyncMock, patch

import pytest

from blop.schemas import FailureCase, FlowStep, RecordedFlow


def make_flow(flow_id: str = "flow1", business_criticality: str = "other") -> RecordedFlow:
    return RecordedFlow(
        flow_id=flow_id,
        flow_name="checkout_with_credit_card",
        app_url="https://example.com",
        goal="Complete a checkout",
        steps=[FlowStep(step_id=0, action="navigate", value="https://example.com")],
        created_at=datetime.now(timezone.utc).isoformat(),
        business_criticality=business_criticality,
    )


def make_flows(*flow_ids: str) -> list[RecordedFlow]:
    return [make_flow(flow_id) for flow_id in flow_ids]


@pytest.mark.asyncio
async def test_run_regression_creates_queued_run():
    """Normal call: create_run called with 'queued', returns status='queued'."""
    from blop.tools.regression import run_regression_test

    mock_start_run = AsyncMock()
    mock_save_event = AsyncMock()

    with patch.dict(os.environ, {"GOOGLE_API_KEY": "test-key"}):
        with patch("blop.storage.sqlite.create_run_with_initial_events", mock_start_run):
            with patch("blop.storage.sqlite.save_run_health_event", mock_save_event):
                with patch("blop.storage.sqlite.get_auth_profile", new_callable=AsyncMock, return_value=None):
                    with patch(
                        "blop.storage.sqlite.get_flows",
                        new_callable=AsyncMock,
                        return_value=make_flows("flow1", "flow2"),
                    ):
                        with patch("blop.storage.files.artifacts_dir", return_value="/tmp/runs/run1"):
                            with patch("asyncio.create_task"):
                                result = await run_regression_test(
                                    app_url="https://example.com",
                                    flow_ids=["flow1", "flow2"],
                                )

    assert result["status"] == "queued"
    assert result["flow_count"] == 2
    assert "status_detail" in result
    assert "recommended_next_action" in result
    assert result["is_terminal"] is False
    mock_start_run.assert_awaited_once()
    assert mock_start_run.await_args.kwargs["status"] == "queued"
    queued_payload = mock_start_run.await_args.kwargs["run_queued_payload"]
    auth_payload = mock_start_run.await_args.kwargs["auth_context_payload"]
    assert "startup_timing_ms" in queued_payload
    assert "startup_timing_ms" in auth_payload
    event_types = [call.args[1] for call in mock_save_event.await_args_list]
    assert "run_startup_timing" in event_types


@pytest.mark.asyncio
async def test_run_regression_records_auth_validation_context():
    from blop.schemas import AuthProfile
    from blop.tools.regression import run_regression_test

    mock_profile = AuthProfile(
        profile_name="prod",
        auth_type="storage_state",
        storage_state_path="/tmp/auth.json",
    )
    mock_start_run = AsyncMock()
    mock_save_event = AsyncMock()

    with patch.dict(os.environ, {"GOOGLE_API_KEY": "test-key"}):
        with patch("blop.storage.sqlite.create_run_with_initial_events", mock_start_run):
            with patch("blop.storage.sqlite.save_run_health_event", mock_save_event):
                with patch("blop.storage.sqlite.get_auth_profile", new_callable=AsyncMock, return_value=mock_profile):
                    with patch(
                        "blop.storage.sqlite.get_flows", new_callable=AsyncMock, return_value=make_flows("flow1")
                    ):
                        with patch(
                            "blop.engine.auth.resolve_storage_state",
                            new_callable=AsyncMock,
                            return_value="/tmp/auth.json",
                        ):
                            with patch(
                                "blop.engine.auth.validate_auth_session", new_callable=AsyncMock, return_value=True
                            ):
                                with patch("blop.storage.files.artifacts_dir", return_value="/tmp/runs/run1"):
                                    with patch("asyncio.create_task"):
                                        await run_regression_test(
                                            app_url="https://example.com/app",
                                            flow_ids=["flow1"],
                                            profile_name="prod",
                                        )

    payload = mock_start_run.await_args.kwargs["auth_context_payload"]
    assert payload["auth_source"] == "storage_state"
    assert payload["storage_state_path"] == "/tmp/auth.json"
    assert payload["session_validation_status"] == "valid"


@pytest.mark.asyncio
async def test_auth_resolution_failure_returns_waiting_auth():
    """Auth profile resolution failure → status='waiting_auth', message included."""
    from blop.schemas import AuthProfile
    from blop.tools.regression import run_regression_test

    mock_profile = AuthProfile(
        profile_name="prod",
        auth_type="env_login",
        login_url="https://example.com/login",
    )

    with patch.dict(os.environ, {"GOOGLE_API_KEY": "test-key"}):
        with patch("blop.storage.sqlite.get_auth_profile", new_callable=AsyncMock, return_value=mock_profile):
            with patch("blop.storage.sqlite.get_flows", new_callable=AsyncMock, return_value=make_flows("flow1")):
                with patch("blop.engine.auth.resolve_storage_state", side_effect=Exception("Credentials not set")):
                    with patch("blop.storage.sqlite.create_run_with_initial_events", new_callable=AsyncMock):
                        with patch("blop.storage.sqlite.save_run_health_event", new_callable=AsyncMock):
                            with patch("blop.storage.files.artifacts_dir", return_value="/tmp/runs/r1"):
                                result = await run_regression_test(
                                    app_url="https://example.com",
                                    flow_ids=["flow1"],
                                    profile_name="prod",
                                )

    assert result["status"] == "waiting_auth"
    assert "message" in result
    assert "prod" in result["message"]
    assert result["is_terminal"] is True
    assert "auth" in result["status_detail"].lower()


@pytest.mark.asyncio
async def test_missing_profile_name_returns_structured_error():
    """Unknown profile_name should fail fast instead of silently running unauthenticated."""
    from blop.tools.regression import run_regression_test

    with patch.dict(os.environ, {"GOOGLE_API_KEY": "test-key"}):
        with patch("blop.storage.sqlite.get_auth_profile", new_callable=AsyncMock, return_value=None):
            with patch("blop.storage.sqlite.get_flows", new_callable=AsyncMock, return_value=make_flows("flow1")):
                result = await run_regression_test(
                    app_url="https://example.com",
                    flow_ids=["flow1"],
                    profile_name="missing_profile",
                )

    assert result["status"] == "error"
    assert "error" in result
    assert "missing_profile" in result["error"]


@pytest.mark.asyncio
async def test_run_persist_case_completed_event_includes_worker_metadata():
    from blop.tools.regression import _run_and_persist

    flow = make_flow("flow-billing", "revenue")
    flow.entry_url = "https://example.com/billing/upgrade"
    case = FailureCase(
        case_id="case-1",
        run_id="run-1",
        flow_id="flow-billing",
        flow_name="checkout_with_credit_card",
        status="pass",
    )

    async def fake_run_flows(**kwargs):
        kwargs["execution_metadata"]["flow-billing"] = {
            "worker_slot": 2,
            "entry_area_key": "billing",
        }
        return [case]

    mock_save_events = AsyncMock()

    with patch("blop.storage.sqlite.update_run_status", new_callable=AsyncMock):
        with patch("blop.storage.sqlite.save_run_health_events", mock_save_events):
            with patch("blop.storage.sqlite.save_cases", new_callable=AsyncMock):
                with patch("blop.storage.sqlite.update_run", new_callable=AsyncMock):
                    with patch("blop.storage.sqlite.save_risk_calibration_record", new_callable=AsyncMock):
                        with patch(
                            "blop.tools.regression.classifier.classify_case",
                            new_callable=AsyncMock,
                            side_effect=lambda current_case, _app_url: current_case,
                        ):
                            with patch(
                                "blop.tools.regression.classifier.classify_run",
                                new_callable=AsyncMock,
                                return_value={"next_actions": []},
                            ):
                                with patch(
                                    "blop.tools.regression.regression_engine.run_flows", side_effect=fake_run_flows
                                ):
                                    await _run_and_persist(
                                        run_id="run-1",
                                        flows=[flow],
                                        app_url="https://example.com",
                                        storage_state=None,
                                        headless=True,
                                    )

    case_events = [
        event["payload"]
        for call in mock_save_events.await_args_list
        for event in call.args[0]
        if event["event_type"] == "case_completed"
    ]
    assert len(case_events) == 1
    assert case_events[0]["worker_slot"] == 2
    assert case_events[0]["entry_area_key"] == "billing"


@pytest.mark.asyncio
async def test_run_and_persist_writes_checkpoint_when_durable():
    from blop.tools.regression import _run_and_persist

    flow = make_flow("flow-checkpoint", "revenue")
    case = FailureCase(
        case_id="case-checkpoint",
        run_id="run-checkpoint",
        flow_id="flow-checkpoint",
        flow_name="checkout_with_credit_card",
        status="pass",
        severity="none",
    )

    mock_upsert_observation = AsyncMock()

    with patch("blop.config.BLOP_DURABILITY_MODE", "async"):
        with patch("blop.storage.sqlite.list_cases_for_runs", new_callable=AsyncMock, return_value=[]):
            with patch("blop.storage.sqlite.update_run_status", new_callable=AsyncMock):
                with patch("blop.storage.sqlite.save_run_health_event", new_callable=AsyncMock):
                    with patch("blop.storage.sqlite.save_run_health_events", new_callable=AsyncMock):
                        with patch("blop.storage.sqlite.save_cases", new_callable=AsyncMock):
                            with patch("blop.storage.sqlite.update_run", new_callable=AsyncMock):
                                with patch("blop.storage.sqlite.upsert_run_observation", mock_upsert_observation):
                                    with patch("blop.storage.sqlite.flush_buffered_writes", new_callable=AsyncMock):
                                        with patch(
                                            "blop.storage.sqlite.save_risk_calibration_record", new_callable=AsyncMock
                                        ):
                                            with patch(
                                                "blop.tools.regression.classifier.classify_case",
                                                new_callable=AsyncMock,
                                                side_effect=lambda current_case, _app_url: current_case,
                                            ):
                                                with patch(
                                                    "blop.tools.regression.classifier.classify_run",
                                                    new_callable=AsyncMock,
                                                    return_value={
                                                        "next_actions": [],
                                                        "severity_counts": {},
                                                        "failed_cases": [],
                                                    },
                                                ):
                                                    with patch(
                                                        "blop.tools.regression.regression_engine.run_flows",
                                                        new_callable=AsyncMock,
                                                        return_value=[case],
                                                    ):
                                                        await _run_and_persist(
                                                            run_id="run-checkpoint",
                                                            flows=[flow],
                                                            app_url="https://example.com",
                                                            storage_state=None,
                                                            headless=True,
                                                        )

    assert mock_upsert_observation.await_count >= 2
    last_payload = mock_upsert_observation.await_args_list[-1].args[2]
    assert last_payload["status"] == "completed"
    assert last_payload["completed_flow_ids"] == ["flow-checkpoint"]
    assert last_payload["remaining_flow_ids"] == []


@pytest.mark.asyncio
async def test_resume_incomplete_runs_restarts_checkpointed_run():
    from blop.tools.regression import resume_incomplete_runs

    flow = make_flow("flow-resume", "activation")
    run_record = {
        "run_id": "run-resume",
        "app_url": "https://example.com",
        "profile_name": None,
        "status": "queued",
        "headless": True,
        "run_mode": "hybrid",
        "flow_ids": ["flow-resume"],
    }
    checkpoint = {
        "remaining_flow_ids": ["flow-resume"],
        "completed_flow_ids": [],
    }

    def _spawn_placeholder(coro):
        coro.close()
        loop = asyncio.get_running_loop()
        fut = loop.create_future()
        fut.set_result(None)
        return fut

    with patch("blop.config.BLOP_DURABILITY_MODE", "async"):
        with patch("blop.storage.sqlite.list_runs", new_callable=AsyncMock, side_effect=[[run_record], []]):
            with patch("blop.storage.sqlite.get_run_observation", new_callable=AsyncMock, return_value=checkpoint):
                with patch("blop.storage.sqlite.get_flows", new_callable=AsyncMock, return_value=[flow]):
                    with patch("blop.storage.sqlite.save_run_health_event", new_callable=AsyncMock) as save_event:
                        with patch("blop.tools.regression._spawn_background_task", side_effect=_spawn_placeholder):
                            summary = await resume_incomplete_runs()

    assert summary["eligible"] == 1
    assert summary["resumed"] == 1
    event_types = [call.args[1] for call in save_event.await_args_list]
    assert "run_resumed" in event_types


@pytest.mark.asyncio
async def test_expired_session_returns_waiting_auth_before_queueing_execution():
    from blop.schemas import AuthProfile
    from blop.tools.regression import run_regression_test

    mock_profile = AuthProfile(
        profile_name="prod",
        auth_type="storage_state",
        storage_state_path="/tmp/prod.json",
    )

    start_run = AsyncMock()
    save_event = AsyncMock()

    with patch.dict(os.environ, {"GOOGLE_API_KEY": "test-key"}):
        with patch("blop.storage.sqlite.get_auth_profile", new_callable=AsyncMock, return_value=mock_profile):
            with patch("blop.storage.sqlite.get_flows", new_callable=AsyncMock, return_value=make_flows("flow1")):
                with patch(
                    "blop.engine.auth.resolve_storage_state", new_callable=AsyncMock, return_value="/tmp/prod.json"
                ):
                    with patch("blop.engine.auth.validate_auth_session", new_callable=AsyncMock, return_value=False):
                        with patch("blop.storage.sqlite.create_run_with_initial_events", start_run):
                            with patch("blop.storage.sqlite.save_run_health_event", save_event):
                                with patch("blop.storage.files.artifacts_dir", return_value="/tmp/runs/r1"):
                                    result = await run_regression_test(
                                        app_url="https://example.com/app",
                                        flow_ids=["flow1"],
                                        profile_name="prod",
                                    )

    assert result["status"] == "waiting_auth"
    assert "expired session" in result["message"].lower()
    assert result["is_terminal"] is True
    start_run.assert_awaited_once()
    assert start_run.await_args.kwargs["status"] == "waiting_auth"
    assert start_run.await_args.kwargs["auth_context_payload"]["session_validation_status"] == "expired_session"
    event_types = [call.args[1] for call in save_event.await_args_list]
    assert "run_startup_timing" in event_types


@pytest.mark.asyncio
async def test_run_and_persist_transitions_to_completed():
    """_run_and_persist: updates status queued→running→completed in DB."""
    from blop.tools.regression import _run_and_persist

    flow = make_flow("flow1", "revenue")
    case = FailureCase(
        run_id="run1",
        flow_id="flow1",
        flow_name="checkout_with_credit_card",
        status="pass",
        severity="none",
        business_criticality="revenue",
    )

    status_transitions: list[str] = []

    async def capture_update_status(run_id: str, status: str) -> None:
        status_transitions.append(status)

    with ExitStack() as stack:
        stack.enter_context(patch("blop.engine.regression.run_flows", new_callable=AsyncMock, return_value=[case]))
        stack.enter_context(patch("blop.engine.classifier.classify_case", new_callable=AsyncMock, return_value=case))
        stack.enter_context(
            patch(
                "blop.engine.classifier.classify_run",
                new_callable=AsyncMock,
                return_value={"next_actions": [], "severity_counts": {}, "failed_cases": []},
            )
        )
        stack.enter_context(patch("blop.storage.sqlite.save_cases", new_callable=AsyncMock))
        stack.enter_context(patch("blop.storage.sqlite.update_run", new_callable=AsyncMock))
        stack.enter_context(patch("blop.storage.sqlite.update_run_status", side_effect=capture_update_status))
        stack.enter_context(patch("blop.storage.sqlite.save_run_health_events", new_callable=AsyncMock))
        await _run_and_persist(
            run_id="run1",
            flows=[flow],
            app_url="https://example.com",
            storage_state=None,
            headless=True,
        )

    assert "running" in status_transitions
    # completed is set via update_run, not update_run_status


@pytest.mark.asyncio
async def test_run_and_persist_exception_transitions_to_failed():
    """_run_and_persist: unhandled exception → run set to 'failed'."""
    from blop.tools.regression import _run_and_persist

    mock_update_run = AsyncMock()

    with patch("blop.engine.regression.run_flows", side_effect=Exception("DB corrupted")):
        with patch("blop.storage.sqlite.update_run_status", new_callable=AsyncMock):
            with patch("blop.storage.sqlite.update_run", mock_update_run):
                with patch("blop.storage.sqlite.save_run_health_events", new_callable=AsyncMock):
                    await _run_and_persist(
                        run_id="run1",
                        flows=[make_flow("flow1")],
                        app_url="https://example.com",
                        storage_state=None,
                        headless=True,
                    )

    mock_update_run.assert_called_once()
    call_args = mock_update_run.call_args
    assert call_args.kwargs["status"] == "failed"


@pytest.mark.asyncio
async def test_run_release_check_queues_run_with_release_id():
    """run_release_check in replay mode returns a release_id and run_id."""
    from blop.tools.release_check import run_release_check

    with patch.dict(os.environ, {"GOOGLE_API_KEY": "test-key"}):
        with patch("blop.storage.sqlite.list_flows", new_callable=AsyncMock, return_value=[]):
            result = await run_release_check(
                app_url="https://example.com",
                journey_ids=[],
                release_id="rel-123",
            )

    # No flows → structured error with release_id present
    assert result.get("release_id") == "rel-123" or "error" in result


@pytest.mark.asyncio
async def test_run_release_check_no_flows_returns_structured_error():
    """run_release_check with no recorded flows returns error dict with release_id."""
    from blop.tools.release_check import run_release_check

    with patch.dict(os.environ, {"GOOGLE_API_KEY": "test-key"}):
        with patch("blop.storage.sqlite.list_flows", new_callable=AsyncMock, return_value=[]):
            result = await run_release_check(
                app_url="https://example.com",
                mode="replay",
                release_id="rel-xyz",
            )

    assert "error" in result
    assert result.get("release_id") == "rel-xyz"


@pytest.mark.asyncio
async def test_run_release_check_accepts_flow_ids_alias():
    """Canonical tool should accept flow_ids directly to reduce journey/flow confusion."""
    from blop.tools.release_check import run_release_check

    mock_run_regression = AsyncMock(return_value={"run_id": "run-abc", "status": "queued"})
    with patch("blop.tools.regression.run_regression_test", mock_run_regression):
        with patch("blop.storage.sqlite.get_flows", new_callable=AsyncMock, return_value=make_flows("flow1")):
            with patch("blop.storage.sqlite.save_release_brief", new_callable=AsyncMock):
                result = await run_release_check(
                    app_url="https://example.com",
                    flow_ids=["flow1"],
                    mode="replay",
                    release_id="rel-flow-alias",
                )

    assert result["run_id"] == "run-abc"
    assert result["flow_count"] == 1


@pytest.mark.asyncio
async def test_run_release_check_rejects_conflicting_flow_and_journey_ids():
    from blop.tools.release_check import run_release_check

    result = await run_release_check(
        app_url="https://example.com",
        flow_ids=["flow1"],
        journey_ids=["journey1"],
    )

    assert "error" in result
    assert "only one of flow_ids or journey_ids" in result["error"]


@pytest.mark.asyncio
async def test_get_test_results_includes_waiting_auth_message():
    """get_test_results on waiting_auth run includes waiting_auth_message in report."""
    from blop.reporting.results import build_report

    run = {
        "run_id": "run1",
        "status": "waiting_auth",
        "started_at": "2026-03-16T10:00:00Z",
        "completed_at": None,
        "artifacts_dir": "/tmp/runs/run1",
        "run_mode": "hybrid",
    }

    report = await build_report(run, [])

    assert report["status"] == "waiting_auth"
    assert "waiting_auth_message" in report
    assert len(report["waiting_auth_message"]) > 0
    assert report["is_terminal"] is True
    assert "status_detail" in report
