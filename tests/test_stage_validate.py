"""Tests for ValidateStage (Stage 1)."""

from unittest.mock import patch

import pytest

from blop.engine.errors import StageError
from blop.engine.pipeline import RunContext
from blop.engine.stages.validate import ValidateStage


@pytest.mark.asyncio
async def test_validate_stage_ok():
    ctx = RunContext(run_id="r1", app_url="https://example.com", flow_ids=[], profile_name=None)
    with patch("blop.engine.stages.validate.validate_app_url", return_value="https://example.com"):
        await ValidateStage().run(ctx)
    assert ctx.validated_url == "https://example.com"
    event_types = [e.event_type for e in ctx.bus.events]
    assert "VALIDATE_START" in event_types
    assert "VALIDATE_OK" in event_types


@pytest.mark.asyncio
async def test_validate_stage_bad_url_raises_stage_error():
    ctx = RunContext(run_id="r2", app_url="not-a-url", flow_ids=[], profile_name=None)
    with patch("blop.engine.stages.validate.validate_app_url", side_effect=ValueError("invalid scheme")):
        with pytest.raises(StageError) as exc_info:
            await ValidateStage().run(ctx)
    assert exc_info.value.stage == "VALIDATE"
    assert exc_info.value.retry_safe is False
    assert exc_info.value.suggested_fix != ""
    assert any(e.event_type == "VALIDATE_FAIL" for e in ctx.bus.events)


@pytest.mark.asyncio
async def test_validate_stage_strips_trailing_slash():
    ctx = RunContext(run_id="r3", app_url="https://example.com/", flow_ids=[], profile_name=None)
    with patch("blop.engine.stages.validate.validate_app_url", return_value="https://example.com/"):
        await ValidateStage().run(ctx)
    assert not (ctx.validated_url or "").endswith("/")
