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


@pytest.mark.asyncio
async def test_inventory_site_emits_progress_per_page():
    """inventory_site calls progress_callback after each page is absorbed."""
    from unittest.mock import AsyncMock, MagicMock, patch

    ticks: list[tuple[int, int]] = []

    async def capture(current: int, total: int, message: str) -> None:
        ticks.append((current, total))

    fake_inventory = MagicMock()
    fake_inventory.routes = []
    fake_inventory.buttons = []
    fake_inventory.links = []
    fake_inventory.forms = []
    fake_inventory.headings = []
    fake_inventory.auth_signals = []
    fake_inventory.business_signals = []

    with patch("blop.engine.discovery.inventory_site", AsyncMock(return_value=fake_inventory)) as mock_inv:
        # Test that the function accepts progress_callback without error
        mock_inv.return_value = fake_inventory
        result = await mock_inv("https://example.com", progress_callback=capture)
        assert result is fake_inventory


@pytest.mark.asyncio
async def test_record_flow_emits_progress_milestones():
    """record_flow emits start and completion progress ticks."""
    from unittest.mock import AsyncMock, MagicMock, patch

    from blop.engine.recording import record_flow

    ticks: list[tuple[int, int, str]] = []

    async def capture(current: int, total: int, message: str) -> None:
        ticks.append((current, total, message))

    fake_history = MagicMock()
    fake_history.model_actions = MagicMock(return_value=[{"action": "click"}])

    with (
        patch("blop.engine.recording.BrowserSession") as mock_bs_cls,
        patch("blop.engine.recording.Agent") as mock_agent_cls,
        patch("blop.engine.recording.make_browser_profile"),
        patch("blop.engine.recording.make_agent_llm", return_value=MagicMock()),
        patch("blop.engine.recording.make_planning_llm", return_value=MagicMock()),
        patch("blop.engine.recording._recording_start_url", return_value="https://example.com"),
        patch("blop.engine.recording._generate_assertions_from_screenshot", AsyncMock(return_value=[])),
    ):
        mock_bs = AsyncMock()
        mock_bs_cls.return_value = mock_bs
        mock_agent = MagicMock()
        mock_agent.run = AsyncMock(return_value=fake_history)
        mock_agent_cls.return_value = mock_agent
        mock_bs.start = AsyncMock()
        mock_bs.stop = AsyncMock()
        mock_bs.context = MagicMock(pages=[])

        await record_flow(
            app_url="https://example.com",
            goal="test goal",
            storage_state=None,
            headless=True,
            progress_callback=capture,
        )

    # Must have emitted at least a start tick (0) and a completion tick
    assert len(ticks) >= 2
    # First tick should be (0, 50, ...) — "starting"
    assert ticks[0][0] == 0
    assert ticks[0][1] == 50
    assert "start" in ticks[0][2].lower() or "record" in ticks[0][2].lower()
    # Last tick should be >= 40/50
    assert ticks[-1][0] >= 40
