# tests/test_pipeline_integration.py
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from blop.engine.errors import StageError
from blop.engine.pipeline import RunContext, build_default_pipeline


@pytest.mark.asyncio
async def test_full_pipeline_happy_path_emits_all_stage_events():
    ctx = RunContext(run_id="int_01", app_url="https://example.com", flow_ids=["f1"], profile_name=None)
    fake_case = MagicMock(status="passed", step_results=[], flow_id="f1", case_id="c1")
    fake_classified = MagicMock(severity="LOW", failure_taxonomy=None)

    with (
        patch("blop.engine.stages.validate.validate_app_url", return_value="https://example.com"),
        patch("blop.engine.stages.execute.run_flows", new=AsyncMock(return_value=[fake_case])),
        patch("blop.engine.stages.classify.classify_run", new=AsyncMock(return_value=[fake_classified])),
        patch(
            "blop.engine.stages.report.build_report",
            return_value={"decision": "SHIP", "blocker_count": 0, "total_cases": 1},
        ),
    ):
        await build_default_pipeline().run(ctx)

    assert ctx.report["decision"] == "SHIP"
    event_types = [e.event_type for e in ctx.bus.events]
    for expected in [
        "VALIDATE_START",
        "VALIDATE_OK",
        "AUTH_START",
        "AUTH_OK",
        "EXECUTE_START",
        "EXECUTE_DONE",
        "CLASSIFY_START",
        "CLASSIFY_OK",
        "REPORT_READY",
    ]:
        assert expected in event_types, f"Missing event: {expected}"
    assert "PIPELINE_ABORT" not in event_types


@pytest.mark.asyncio
async def test_full_pipeline_auth_failure_aborts_before_execute():
    ctx = RunContext(run_id="int_02", app_url="https://example.com", flow_ids=["f1"], profile_name="staging")

    with (
        patch("blop.engine.stages.validate.validate_app_url", return_value="https://example.com"),
        patch(
            "blop.engine.stages.auth.resolve_storage_state",
            new=AsyncMock(side_effect=Exception("profile missing")),
        ),
    ):
        with pytest.raises(StageError) as exc_info:
            await build_default_pipeline().run(ctx)

    assert exc_info.value.stage == "AUTH"
    assert "staging" in exc_info.value.suggested_fix
    event_types = [e.event_type for e in ctx.bus.events]
    assert "PIPELINE_ABORT" in event_types
    assert "EXECUTE_START" not in event_types
    abort = [e for e in ctx.bus.events if e.event_type == "PIPELINE_ABORT"][0]
    assert abort.details["suggested_fix"] != ""
