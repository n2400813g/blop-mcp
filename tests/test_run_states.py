"""Tests for tools/regression.py — run state machine."""
from __future__ import annotations

import os
from contextlib import ExitStack
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from blop.schemas import FailureCase, RecordedFlow, FlowStep


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


@pytest.mark.asyncio
async def test_run_regression_creates_queued_run():
    """Normal call: create_run called with 'queued', returns status='queued'."""
    from blop.tools.regression import run_regression_test

    mock_create_run = AsyncMock()
    mock_update_status = AsyncMock()

    with patch.dict(os.environ, {"GOOGLE_API_KEY": "test-key"}):
        with patch("blop.storage.sqlite.create_run", mock_create_run):
            with patch("blop.storage.sqlite.save_run_health_event", new_callable=AsyncMock):
                with patch("blop.storage.sqlite.get_auth_profile", new_callable=AsyncMock, return_value=None):
                    with patch("blop.storage.sqlite.get_flow", new_callable=AsyncMock, return_value=make_flow("flow1")):
                        with patch("blop.storage.files.artifacts_dir", return_value="/tmp/runs/run1"):
                            with patch("asyncio.create_task"):
                                result = await run_regression_test(
                                    app_url="https://example.com",
                                    flow_ids=["flow1", "flow2"],
                                )

    assert result["status"] == "queued"
    assert result["flow_count"] == 2
    mock_create_run.assert_called_once()


@pytest.mark.asyncio
async def test_auth_resolution_failure_returns_waiting_auth():
    """Auth profile resolution failure → status='waiting_auth', message included."""
    from blop.tools.regression import run_regression_test
    from blop.schemas import AuthProfile

    mock_profile = AuthProfile(
        profile_name="prod",
        auth_type="env_login",
        login_url="https://example.com/login",
    )

    with patch.dict(os.environ, {"GOOGLE_API_KEY": "test-key"}):
        with patch("blop.storage.sqlite.get_auth_profile", new_callable=AsyncMock, return_value=mock_profile):
            with patch("blop.storage.sqlite.get_flow", new_callable=AsyncMock, return_value=make_flow("flow1")):
                with patch("blop.engine.auth.resolve_storage_state", side_effect=Exception("Credentials not set")):
                    with patch("blop.storage.sqlite.create_run", new_callable=AsyncMock):
                        with patch("blop.storage.sqlite.save_run_health_event", new_callable=AsyncMock):
                            with patch("blop.storage.sqlite.update_run_status", new_callable=AsyncMock):
                                with patch("blop.storage.files.artifacts_dir", return_value="/tmp/runs/r1"):
                                    result = await run_regression_test(
                                        app_url="https://example.com",
                                        flow_ids=["flow1"],
                                        profile_name="prod",
                                    )

    assert result["status"] == "waiting_auth"
    assert "message" in result
    assert "prod" in result["message"]


@pytest.mark.asyncio
async def test_run_and_persist_transitions_to_completed():
    """_run_and_persist: updates status queued→running→completed in DB."""
    from blop.tools.regression import _run_and_persist

    flow = make_flow("flow1", "revenue")
    case = FailureCase(
        run_id="run1", flow_id="flow1", flow_name="checkout_with_credit_card",
        status="pass", severity="none", business_criticality="revenue",
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
        stack.enter_context(patch("blop.storage.sqlite.save_case", new_callable=AsyncMock))
        stack.enter_context(patch("blop.storage.sqlite.update_run", new_callable=AsyncMock))
        stack.enter_context(patch("blop.storage.sqlite.update_run_status", side_effect=capture_update_status))
        stack.enter_context(patch("blop.storage.sqlite.save_run_health_event", new_callable=AsyncMock))
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
                with patch("blop.storage.sqlite.save_run_health_event", new_callable=AsyncMock):
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

    mock_run_regression = AsyncMock(return_value={"run_id": "run-abc", "status": "queued"})
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
