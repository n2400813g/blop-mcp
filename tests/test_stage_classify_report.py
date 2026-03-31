# tests/test_stage_classify_report.py
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from blop.engine.pipeline import RunContext
from blop.engine.stages.classify import ClassifyStage
from blop.engine.stages.report import ReportStage


def _ctx_with_cases():
    ctx = RunContext(run_id="r1", app_url="https://example.com", flow_ids=["f1"], profile_name=None)
    case = MagicMock()
    case.status = "failed"
    case.flow_id = "f1"
    case.case_id = "c1"
    ctx.step_results = [case]
    return ctx


@pytest.mark.asyncio
async def test_classify_stage_emits_events_and_stores_cases():
    ctx = _ctx_with_cases()
    classified = MagicMock()
    classified.failure_taxonomy = "GENUINE_REGRESSION"
    classified.severity = "BLOCKER"
    with patch("blop.engine.stages.classify.classify_run", new=AsyncMock(return_value=[classified])):
        await ClassifyStage().run(ctx)
    event_types = [e.event_type for e in ctx.bus.events]
    assert "CLASSIFY_START" in event_types
    assert "CLASSIFY_OK" in event_types
    assert len(ctx.classified_cases) == 1


@pytest.mark.asyncio
async def test_report_stage_emits_report_ready_with_decision():
    ctx = _ctx_with_cases()
    classified = MagicMock()
    classified.severity = "BLOCKER"
    ctx.classified_cases = [classified]
    with patch(
        "blop.engine.stages.report.build_report",
        return_value={"decision": "BLOCK", "blocker_count": 1, "total_cases": 1},
    ):
        await ReportStage().run(ctx)
    assert ctx.report["decision"] == "BLOCK"
    rr_events = [e for e in ctx.bus.events if e.event_type == "REPORT_READY"]
    assert len(rr_events) == 1
    assert rr_events[0].details["decision"] == "BLOCK"
