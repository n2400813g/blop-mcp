"""RunContext dataclass and RunPipeline orchestrator."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable

from blop.engine.errors import StageError
from blop.engine.events import EventBus


@dataclass
class RunContext:
    """Mutable context threaded through all pipeline stages for one run."""

    run_id: str
    app_url: str
    flow_ids: list[str]
    profile_name: str | None
    # Populated by stages:
    validated_url: str | None = None
    browser_config: dict[str, Any] = field(default_factory=dict)
    auth_state: str | None = None  # path to storage_state JSON or None
    step_results: list[Any] = field(default_factory=list)
    classified_cases: list[Any] = field(default_factory=list)
    report: dict[str, Any] | None = None
    # Event bus — created in __post_init__
    bus: EventBus = field(init=False)

    def __post_init__(self) -> None:
        self.bus = EventBus(self.run_id)


@runtime_checkable
class Stage(Protocol):
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

    async def run(self, ctx: RunContext) -> None:
        stage_order = [
            ("VALIDATE", self.validate),
            ("AUTH", self.auth),
            ("EXECUTE", self.execute),
            ("CLASSIFY", self.classify),
            ("REPORT", self.report),
        ]
        for stage_name, stage in stage_order:
            try:
                await stage.run(ctx)
            except StageError as exc:
                ctx.bus.emit(
                    "PIPELINE",
                    "PIPELINE_ABORT",
                    f"Pipeline aborted at {stage_name}: {exc.message}",
                    details={
                        "stage": exc.stage,
                        "code": exc.code,
                        "likely_cause": exc.likely_cause,
                        "suggested_fix": exc.suggested_fix,
                        "retry_safe": exc.retry_safe,
                    },
                )
                raise


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
    import asyncio

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
