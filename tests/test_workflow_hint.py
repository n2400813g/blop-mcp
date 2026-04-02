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
