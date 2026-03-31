# tests/test_stage_execute.py
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from blop.engine.errors import StageError
from blop.engine.pipeline import RunContext
from blop.engine.stages.execute import ExecuteStage


def _make_case(flow_id="f1", status="passed", step_results=None):
    case = MagicMock()
    case.flow_id = flow_id
    case.status = status
    case.step_results = step_results or []
    case.case_id = "case_001"
    return case


@pytest.mark.asyncio
async def test_execute_stage_emits_start_and_done():
    ctx = RunContext(run_id="r1", app_url="https://example.com", flow_ids=["f1"], profile_name=None)
    ctx.validated_url = "https://example.com"
    with patch("blop.engine.stages.execute.run_flows", new=AsyncMock(return_value=[_make_case()])):
        await ExecuteStage().run(ctx)
    event_types = [e.event_type for e in ctx.bus.events]
    assert "EXECUTE_START" in event_types
    assert "EXECUTE_DONE" in event_types
    assert len(ctx.step_results) == 1


@pytest.mark.asyncio
async def test_execute_stage_emits_step_fail_with_details():
    ctx = RunContext(run_id="r2", app_url="https://example.com", flow_ids=["f1"], profile_name=None)
    ctx.validated_url = "https://example.com"

    step = MagicMock()
    step.status = "failed"
    step.selector = "button.checkout"
    step.action = "click"
    step.screenshot_path = "runs/screenshots/r2/step_01.png"
    step.console_errors = ["TypeError: null"]
    step.healed = False
    step.healing_attempted = True

    fake_case = _make_case(status="failed", step_results=[step])
    with patch("blop.engine.stages.execute.run_flows", new=AsyncMock(return_value=[fake_case])):
        await ExecuteStage().run(ctx)

    fail_events = [e for e in ctx.bus.events if e.event_type == "STEP_FAIL"]
    assert len(fail_events) == 1
    assert fail_events[0].details["selector"] == "button.checkout"
    assert fail_events[0].details["action"] == "click"
    assert fail_events[0].details["healing_attempted"] is True


@pytest.mark.asyncio
async def test_execute_stage_wraps_engine_exception_as_stage_error():
    ctx = RunContext(run_id="r3", app_url="https://example.com", flow_ids=["f1"], profile_name=None)
    ctx.validated_url = "https://example.com"
    with patch("blop.engine.stages.execute.run_flows", new=AsyncMock(side_effect=RuntimeError("browser crash"))):
        with pytest.raises(StageError) as exc_info:
            await ExecuteStage().run(ctx)
    assert exc_info.value.stage == "EXECUTE"
    assert exc_info.value.retry_safe is True
