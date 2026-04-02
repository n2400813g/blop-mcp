import pytest

from blop.mcp.envelope import WorkflowHint, build_poll_workflow_hint


def test_workflow_hint_fields():
    hint = WorkflowHint(
        next_action="call get_test_results every 4s",
        poll_recipe={"tool": "get_test_results"},
        estimated_duration_s=(30, 150),
        progress_hint="typically 1-3 min",
    )
    assert hint.next_action == "call get_test_results every 4s"
    assert hint.poll_recipe == {"tool": "get_test_results"}
    assert hint.estimated_duration_s == (30, 150)
    assert hint.progress_hint == "typically 1-3 min"


def test_workflow_hint_optional_fields():
    hint = WorkflowHint(next_action="do something")
    assert hint.poll_recipe is None
    assert hint.estimated_duration_s is None
    assert hint.progress_hint == ""


def test_build_poll_workflow_hint_5_flows():
    hint = build_poll_workflow_hint(run_id="abc123", flow_count=5)
    assert "abc123" in hint.next_action
    assert hint.poll_recipe["tool"] == "get_test_results"
    assert hint.poll_recipe["args_template"] == {"run_id": "abc123"}
    assert "interrupted" in hint.poll_recipe["terminal_statuses"]
    assert hint.poll_recipe["interval_s"] == 4
    assert hint.poll_recipe["timeout_s"] == 900
    assert hint.estimated_duration_s == (50, 225)  # 5 * 10, 5 * 45


def test_build_poll_workflow_hint_zero_flows():
    hint = build_poll_workflow_hint(run_id="xyz", flow_count=0)
    assert hint.estimated_duration_s == (30, 300)  # fallback


def test_workflow_hint_model_dump():
    hint = build_poll_workflow_hint(run_id="r1", flow_count=3)
    d = hint.model_dump()
    assert isinstance(d, dict)
    assert "next_action" in d
    assert "poll_recipe" in d


@pytest.mark.asyncio
async def test_run_regression_test_response_has_workflow(tmp_path, monkeypatch):
    """run_regression_test queued response includes a workflow field with poll_recipe."""
    import uuid
    from unittest.mock import AsyncMock, MagicMock, patch

    monkeypatch.setenv("GOOGLE_API_KEY", "test-key")
    monkeypatch.setenv("BLOP_DB_PATH", str(tmp_path / "test.db"))

    flow_id = uuid.uuid4().hex
    fake_flow = MagicMock()
    fake_flow.flow_id = flow_id
    fake_flow.flow_name = "test_flow"
    fake_flow.business_criticality = "other"
    fake_flow.platform = "web"
    fake_flow.run_mode_override = None

    with (
        patch("blop.tools.regression.sqlite") as mock_sqlite,
        patch("blop.tools.regression._spawn_background_task", return_value=MagicMock(done=lambda: False)),
        patch("blop.tools.regression._register_run_task"),
        patch("blop.tools.regression.file_store.artifacts_dir", return_value="/tmp/artifacts"),
        patch("blop.tools.regression.regression_engine.compute_replay_worker_count", return_value=1),
    ):
        mock_sqlite.get_flows = AsyncMock(return_value=[fake_flow])
        mock_sqlite.get_auth_profile = AsyncMock(return_value=None)
        mock_sqlite.create_run_with_initial_events = AsyncMock()
        mock_sqlite.save_run_health_event = AsyncMock()

        from blop.tools.regression import run_regression_test

        result = await run_regression_test(
            app_url="https://example.com",
            flow_ids=[flow_id],
        )

    assert "workflow" in result, f"Expected 'workflow' key, got keys: {list(result.keys())}"
    wf = result["workflow"]
    assert "next_action" in wf
    assert "poll_recipe" in wf
    assert wf["poll_recipe"]["tool"] == "get_test_results"
    assert wf["poll_recipe"]["args_template"]["run_id"] == result["run_id"]
    assert "interrupted" in wf["poll_recipe"]["terminal_statuses"]


def test_queued_release_check_result_has_workflow():
    """_queued_release_check_result includes workflow with poll_recipe."""
    import sys

    sys.path.insert(0, "src")
    from blop.tools.release_check import _queued_release_check_result

    result = _queued_release_check_result(
        release_id="rel1",
        run_id="run1",
        status="queued",
        flow_ids=["f1", "f2", "f3"],
        selected_flows=[],
        profile_name=None,
        run_mode="replay",
        criticality_filter=["revenue"],
        smoke_summary=None,
    )
    assert "workflow" in result, f"Missing 'workflow'. Keys: {list(result.keys())}"
    wf = result["workflow"]
    assert wf["poll_recipe"]["args_template"]["run_id"] == "run1"
    assert wf["estimated_duration_s"] == (30, 135)  # 3 flows * 10, 3 * 45
