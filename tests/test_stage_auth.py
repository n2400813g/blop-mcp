"""Tests for AuthStage (Stage 2)."""

from unittest.mock import AsyncMock, patch

import pytest

from blop.engine.errors import StageError
from blop.engine.pipeline import RunContext
from blop.engine.stages.auth import AuthStage


@pytest.mark.asyncio
async def test_auth_stage_no_profile():
    ctx = RunContext(run_id="r1", app_url="https://example.com", flow_ids=[], profile_name=None)
    ctx.validated_url = "https://example.com"
    await AuthStage().run(ctx)
    assert ctx.auth_state is None
    event_types = [e.event_type for e in ctx.bus.events]
    assert "AUTH_START" in event_types
    assert "AUTH_OK" in event_types


@pytest.mark.asyncio
async def test_auth_stage_resolves_profile():
    ctx = RunContext(run_id="r2", app_url="https://example.com", flow_ids=[], profile_name="staging")
    ctx.validated_url = "https://example.com"
    with patch("blop.engine.stages.auth.resolve_storage_state", new=AsyncMock(return_value="/tmp/state.json")):
        await AuthStage().run(ctx)
    assert ctx.auth_state == "/tmp/state.json"
    assert any(e.event_type == "AUTH_OK" for e in ctx.bus.events)


@pytest.mark.asyncio
async def test_auth_stage_fail_raises_stage_error_with_profile_name():
    ctx = RunContext(run_id="r3", app_url="https://example.com", flow_ids=[], profile_name="missing_profile")
    ctx.validated_url = "https://example.com"
    with patch("blop.engine.stages.auth.resolve_storage_state", new=AsyncMock(side_effect=Exception("not found"))):
        with pytest.raises(StageError) as exc_info:
            await AuthStage().run(ctx)
    assert exc_info.value.stage == "AUTH"
    assert "missing_profile" in exc_info.value.suggested_fix
    assert any(e.event_type == "AUTH_FAIL" for e in ctx.bus.events)
