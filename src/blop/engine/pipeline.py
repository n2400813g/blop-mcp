"""RunContext dataclass and RunPipeline orchestrator."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, Protocol, runtime_checkable

from blop.engine.errors import StageError
from blop.engine.events import EventBus

ProgressCallback = Callable[[int, int, str], Awaitable[None]] | None


@dataclass
class RunContext:
    """Mutable context threaded through all pipeline stages for one run."""

    run_id: str
    app_url: str
    flow_ids: list[str]
    profile_name: str | None
    # Populated by VALIDATE:
    validated_url: str | None = None
    browser_config: dict[str, Any] = field(default_factory=dict)
    # Populated by AUTH:
    auth_state: str | None = None  # path to storage_state JSON or None
    # EXECUTE inputs (set by tools.regression before pipeline.run for web/mobile replay):
    flows: list[Any] = field(default_factory=list)
    headless: bool = True
    run_mode: str = "hybrid"
    auto_rerecord: bool = False
    mobile_only: bool = False
    execution_metadata: dict[str, dict] | None = None
    on_case_completed: Any = None
    # When True, cases were already passed through classify_case during replay; CLASSIFY only runs classify_run.
    incremental_classify: bool = False
    # When True, AUTH does not re-resolve storage (auth_state already set by caller).
    skip_auth_resolution: bool = False
    artifacts_dir: str = ""
    # Populated by EXECUTE:
    step_results: list[Any] = field(default_factory=list)
    # Populated by CLASSIFY:
    classified_cases: list[Any] = field(default_factory=list)
    run_summary: dict[str, Any] | None = None
    # Populated by REPORT:
    report: dict[str, Any] | None = None
    # Event bus — created in __post_init__
    bus: EventBus = field(init=False)

    def __post_init__(self) -> None:
        self.bus = EventBus(self.run_id)


@runtime_checkable
class Stage(Protocol):
    """A pipeline stage. Mutates ctx in place. Raises StageError on unrecoverable failure."""

    async def run(self, ctx: RunContext) -> None: ...


class RunPipeline:
    """Orchestrates VALIDATE → AUTH → EXECUTE → CLASSIFY → REPORT in order."""

    def __init__(
        self,
        *,
        validate: Stage,
        auth: Stage,
        execute: Stage,
        classify: Stage,
        report: Stage,
    ) -> None:
        self.validate = validate
        self.auth = auth
        self.execute = execute
        self.classify = classify
        self.report = report

    async def run(self, ctx: RunContext, *, progress_callback: ProgressCallback = None) -> None:
        """Run VALIDATE → AUTH → EXECUTE → CLASSIFY → REPORT in order.

        Emits progress ticks via progress_callback(current, total, message) if provided.
        Stage boundaries: VALIDATE=5, AUTH=15, EXECUTE=85, CLASSIFY=95, REPORT=100.
        """
        stage_schedule = [
            ("VALIDATE", self.validate, 5),
            ("AUTH", self.auth, 15),
            ("EXECUTE", self.execute, 85),
            ("CLASSIFY", self.classify, 95),
            ("REPORT", self.report, 100),
        ]
        cumulative = 0
        for stage_name, stage, target_progress in stage_schedule:
            if progress_callback is not None:
                try:
                    await progress_callback(cumulative, 100, f"stage: {stage_name}")
                except Exception:
                    pass
            try:
                await stage.run(ctx)
            except StageError as exc:
                ctx.bus.emit(
                    "PIPELINE",
                    "PIPELINE_ABORT",
                    f"Pipeline aborted at {exc.stage}: {exc.message}",
                    details={
                        "stage": exc.stage,
                        "code": exc.code,
                        "likely_cause": exc.likely_cause,
                        "suggested_fix": exc.suggested_fix,
                        "retry_safe": exc.retry_safe,
                    },
                )
                raise
            cumulative = target_progress
        if progress_callback is not None:
            try:
                await progress_callback(100, 100, "pipeline complete")
            except Exception:
                pass


def build_default_pipeline() -> RunPipeline:
    """Build the standard 5-stage pipeline with production stage implementations."""
    from blop.engine.stages.auth import AuthStage
    from blop.engine.stages.classify import ClassifyStage
    from blop.engine.stages.execute import ExecuteStage
    from blop.engine.stages.report import ReportStage
    from blop.engine.stages.validate import ValidateStage

    return RunPipeline(
        validate=ValidateStage(),
        auth=AuthStage(),
        execute=ExecuteStage(),
        classify=ClassifyStage(),
        report=ReportStage(),
    )


async def persist_bus_events(bus: EventBus) -> None:
    """Persist all events in the bus to the run_health_events table."""

    async def _save(ev: Any) -> None:
        try:
            from blop.storage.sqlite import save_run_health_event

            await save_run_health_event(
                run_id=ev.run_id,
                event_type=ev.event_type,
                payload={
                    "stage": ev.stage,
                    "message": ev.message,
                    "details": ev.details,
                },
            )
        except Exception:
            pass  # best-effort; do not fail the run on persistence errors

    tasks = [_save(ev) for ev in bus.events]
    if tasks:
        await asyncio.gather(*tasks, return_exceptions=True)
