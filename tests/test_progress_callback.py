# tests/test_progress_callback.py
import pytest

from blop.engine.pipeline import RunContext, RunPipeline


@pytest.mark.asyncio
async def test_pipeline_emits_progress_at_stage_boundaries():
    """RunPipeline calls progress_callback at each stage boundary."""
    ticks: list[tuple[int, int, str]] = []

    async def capture_progress(current: int, total: int, message: str) -> None:
        ticks.append((current, total, message))

    class FakeStage:
        def __init__(self, name):
            self.name = name

        async def run(self, ctx):
            pass

    pipeline = RunPipeline(
        validate=FakeStage("validate"),
        auth=FakeStage("auth"),
        execute=FakeStage("execute"),
        classify=FakeStage("classify"),
        report=FakeStage("report"),
    )
    ctx = RunContext(run_id="r1", app_url="https://example.com", flow_ids=[], profile_name=None)
    await pipeline.run(ctx, progress_callback=capture_progress)

    # Should have at least one tick per stage (5 stages) plus final
    assert len(ticks) >= 5
    # Final tick must be 100/100
    assert ticks[-1][0] == 100
    assert ticks[-1][1] == 100
    # All totals must be 100
    for current, total, _ in ticks:
        assert total == 100
    # Progress must be non-decreasing
    values = [t[0] for t in ticks]
    assert values == sorted(values)


@pytest.mark.asyncio
async def test_pipeline_no_error_when_progress_callback_is_none():
    """RunPipeline runs normally with progress_callback=None."""

    class FakeStage:
        async def run(self, ctx):
            pass

    pipeline = RunPipeline(
        validate=FakeStage(),
        auth=FakeStage(),
        execute=FakeStage(),
        classify=FakeStage(),
        report=FakeStage(),
    )
    ctx = RunContext(run_id="r2", app_url="https://example.com", flow_ids=[], profile_name=None)
    await pipeline.run(ctx, progress_callback=None)  # must not raise


@pytest.mark.asyncio
async def test_pipeline_swallows_progress_callback_exception():
    """A progress_callback that raises must not abort the pipeline."""
    calls: list[str] = []

    async def bad_callback(current, total, message):
        raise RuntimeError("progress sink exploded")

    class RecordStage:
        def __init__(self, name):
            self.name = name

        async def run(self, ctx):
            calls.append(self.name)

    pipeline = RunPipeline(
        validate=RecordStage("validate"),
        auth=RecordStage("auth"),
        execute=RecordStage("execute"),
        classify=RecordStage("classify"),
        report=RecordStage("report"),
    )
    ctx = RunContext(run_id="r3", app_url="https://example.com", flow_ids=[], profile_name=None)
    await pipeline.run(ctx, progress_callback=bad_callback)
    assert calls == ["validate", "auth", "execute", "classify", "report"]
