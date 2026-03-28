from __future__ import annotations

import os
from unittest.mock import AsyncMock, patch

import pytest


@pytest.mark.asyncio
async def test_evaluate_web_task_persists_run_artifacts_and_format(tmp_path):
    from blop.storage import sqlite
    from blop.tools import evaluate

    db_path = str(tmp_path / "evaluate.db")
    app_url = "https://example.com"

    fake_report = {
        "summary": ["Task completed successfully"],
        "agent_steps": [{"step": 1, "action": "navigate", "description": "Navigate -> https://example.com"}],
        "evidence": {
            "console_errors": [],
            "console_log_count": 3,
            "network_failures": [],
            "network_request_count": 8,
            "screenshots": ["/tmp/eval_1.png", "/tmp/eval_2.png"],
            "trace_path": "/tmp/eval_trace.zip",
        },
        "pass_fail": "pass",
        "raw_result": "done",
        "elapsed_secs": 1.2,
        "_network_log_path": "/tmp/network.jsonl",
        "_console_log_path": "/tmp/console.log",
    }

    with patch.dict(os.environ, {"BLOP_DB_PATH": db_path}):
        await sqlite.init_db()
        with (
            patch(
                "blop.tools.evaluate.auth_engine.auto_storage_state_from_env",
                new=AsyncMock(return_value=None),
            ),
            patch("blop.tools.evaluate._run_evaluation", new=AsyncMock(return_value=fake_report)),
        ):
            result = await evaluate.evaluate_web_task(
                app_url=app_url,
                task="Open homepage and verify it loads",
                format="markdown",
                save_as_recorded_flow=False,
            )

        run = await sqlite.get_run(result["run_id"])
        artifacts = await sqlite.list_artifacts_for_run(result["run_id"])
        cases = await sqlite.list_cases_for_run(result["run_id"])

    assert run is not None
    assert run["status"] == "completed"
    assert run["completed_at"] is not None
    assert run["run_mode"] == "evaluate"
    assert "formatted_report" in result
    assert result["formatted_report"].startswith("## Web Evaluation Report")
    assert len(cases) == 1
    assert cases[0].status == "pass"
    assert cases[0].severity == "none"

    artifact_types = {a["artifact_type"] for a in artifacts}
    assert "network_log" in artifact_types
    assert "console_log" in artifact_types
    assert "trace" in artifact_types
    assert "screenshot" in artifact_types


@pytest.mark.asyncio
async def test_evaluate_web_task_promotes_to_recorded_flow_when_requested(tmp_path):
    from blop.storage import sqlite
    from blop.tools import evaluate

    db_path = str(tmp_path / "evaluate_promote.db")

    fake_report = {
        "summary": ["Task completed successfully"],
        "agent_steps": [{"step": 1, "action": "click_element", "description": "Click element (index 1)"}],
        "evidence": {
            "console_errors": [],
            "console_log_count": 0,
            "network_failures": [],
            "network_request_count": 0,
            "screenshots": [],
            "trace_path": None,
        },
        "pass_fail": "pass",
        "raw_result": "ok",
        "elapsed_secs": 0.9,
        "_network_log_path": None,
        "_console_log_path": None,
    }

    with patch.dict(os.environ, {"BLOP_DB_PATH": db_path}):
        await sqlite.init_db()
        with patch(
            "blop.tools.evaluate.auth_engine.auto_storage_state_from_env",
            new=AsyncMock(return_value=None),
        ):
            with patch("blop.tools.evaluate._run_evaluation", new=AsyncMock(return_value=fake_report)):
                with patch(
                    "blop.tools.evaluate._promote_to_recorded_flow",
                    new=AsyncMock(return_value=("flow_promoted_123", None)),
                ) as promote_mock:
                    result = await evaluate.evaluate_web_task(
                        app_url="https://example.com",
                        task="Complete signup",
                        save_as_recorded_flow=True,
                        flow_name="signup_smoke",
                        format="json",
                    )

    promote_mock.assert_awaited_once()
    assert result["recorded_flow_id"] == "flow_promoted_123"
    assert result["pass_fail"] == "pass"


@pytest.mark.asyncio
async def test_evaluate_web_task_synthetic_recorded_flow_when_pass_and_no_agent_steps(tmp_path):
    from blop.storage import sqlite
    from blop.tools import evaluate

    db_path = str(tmp_path / "evaluate_synthetic.db")
    fake_report = {
        "summary": ["Task completed successfully"],
        "agent_steps": [],
        "evidence": {
            "console_errors": [],
            "console_log_count": 0,
            "network_failures": [],
            "network_request_count": 0,
            "screenshots": [],
            "trace_path": None,
        },
        "pass_fail": "pass",
        "raw_result": "None",
        "elapsed_secs": 0.5,
        "_network_log_path": None,
        "_console_log_path": None,
    }

    with patch.dict(os.environ, {"BLOP_DB_PATH": db_path}):
        await sqlite.init_db()
        with (
            patch(
                "blop.tools.evaluate.auth_engine.auto_storage_state_from_env",
                new=AsyncMock(return_value=None),
            ),
            patch("blop.tools.evaluate._run_evaluation", new=AsyncMock(return_value=fake_report)),
        ):
            result = await evaluate.evaluate_web_task(
                app_url="https://example.com",
                task="Do something on the page",
                save_as_recorded_flow=True,
                flow_name="synthetic_smoke",
                format="json",
            )

        assert result.get("recorded_flow_id"), result
        assert result.get("recorded_flow_synthetic") is True
        assert result.get("recorded_flow_promotion") == "synthetic_empty_agent_steps"
        loaded = await sqlite.get_flow(result["recorded_flow_id"])
        assert loaded is not None
        assert loaded.flow_name == "synthetic_smoke"
        assert len(loaded.steps) == 3


@pytest.mark.asyncio
async def test_evaluate_web_task_persists_failure_case_for_step_budget_exhaustion(tmp_path):
    from blop.storage import sqlite
    from blop.tools import evaluate
    from blop.tools.results import get_test_results

    db_path = str(tmp_path / "evaluate_fail.db")
    fake_report = {
        "summary": ["Task encountered issues"],
        "agent_steps": [{"step": 1, "action": "navigate", "description": "Navigate -> https://example.com"}],
        "evidence": {
            "console_errors": [],
            "console_log_count": 0,
            "network_failures": [],
            "network_request_count": 0,
            "screenshots": ["/tmp/eval_fail_1.png"],
            "trace_path": "/tmp/eval_fail_trace.zip",
        },
        "pass_fail": "fail",
        "raw_result": "I have reached the maximum number of steps and the task is incomplete.",
        "elapsed_secs": 12.3,
        "_network_log_path": None,
        "_console_log_path": None,
    }

    with patch.dict(os.environ, {"BLOP_DB_PATH": db_path}):
        await sqlite.init_db()
        with (
            patch(
                "blop.tools.evaluate.auth_engine.auto_storage_state_from_env",
                new=AsyncMock(return_value=None),
            ),
            patch("blop.tools.evaluate._run_evaluation", new=AsyncMock(return_value=fake_report)),
        ):
            result = await evaluate.evaluate_web_task(
                app_url="https://example.com",
                task="Explore the public site and report blockers",
                format="json",
            )
            run = await sqlite.get_run(result["run_id"])
            cases = await sqlite.list_cases_for_run(result["run_id"])
            stored_report = await get_test_results(result["run_id"])

    assert run["status"] == "completed"
    assert run["completed_at"] is not None
    assert result["release_recommendation"]["decision"] == "BLOCK"
    assert len(cases) == 1
    assert cases[0].severity == "blocker"
    assert cases[0].failure_class == "test_fragility"
    assert cases[0].failure_reason_codes == ["agent_step_budget_exhausted"]
    assert stored_report["release_recommendation"]["decision"] == "BLOCK"
    assert stored_report["top_failure_mode"] == "automation_fragility"
