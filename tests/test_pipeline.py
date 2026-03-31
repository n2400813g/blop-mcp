import pytest

from blop.engine.errors import StageError
from blop.engine.events import EventBus
from blop.engine.pipeline import RunContext, RunPipeline


def test_run_context_defaults():
    ctx = RunContext(run_id="r1", app_url="https://example.com", flow_ids=["f1"], profile_name=None)
    assert ctx.validated_url is None
    assert ctx.auth_state is None
    assert ctx.step_results == []
    assert ctx.classified_cases == []
    assert ctx.report is None
    assert isinstance(ctx.bus, EventBus)
    assert ctx.bus.run_id == "r1"


@pytest.mark.asyncio
async def test_pipeline_runs_stages_in_order():
    calls: list[str] = []

    class FakeStage:
        def __init__(self, name: str) -> None:
            self.name = name

        async def run(self, ctx: RunContext) -> None:
            calls.append(self.name)

    pipeline = RunPipeline(
        validate=FakeStage("validate"),
        auth=FakeStage("auth"),
        execute=FakeStage("execute"),
        classify=FakeStage("classify"),
        report=FakeStage("report"),
    )
    ctx = RunContext(run_id="r2", app_url="https://example.com", flow_ids=[], profile_name=None)
    await pipeline.run(ctx)
    assert calls == ["validate", "auth", "execute", "classify", "report"]


@pytest.mark.asyncio
async def test_pipeline_aborts_on_stage_error_and_emits_abort():
    calls: list[str] = []

    class PassStage:
        async def run(self, ctx: RunContext) -> None:
            calls.append("pass")

    class FailStage:
        async def run(self, ctx: RunContext) -> None:
            calls.append("fail")
            raise StageError(
                stage="AUTH",
                code="BLOP_AUTH_PROFILE_NOT_FOUND",
                message="not found",
                likely_cause="x",
                suggested_fix="y",
            )

    class NeverStage:
        async def run(self, ctx: RunContext) -> None:
            calls.append("never")

    pipeline = RunPipeline(
        validate=PassStage(),
        auth=FailStage(),
        execute=NeverStage(),
        classify=NeverStage(),
        report=NeverStage(),
    )
    ctx = RunContext(run_id="r3", app_url="https://example.com", flow_ids=[], profile_name=None)
    with pytest.raises(StageError) as exc_info:
        await pipeline.run(ctx)

    assert calls == ["pass", "fail"]
    assert exc_info.value.stage == "AUTH"
    abort_events = [e for e in ctx.bus.events if e.event_type == "PIPELINE_ABORT"]
    assert len(abort_events) == 1
    assert "AUTH" in abort_events[0].message
