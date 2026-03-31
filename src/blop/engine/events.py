"""Typed health events emitted by each pipeline stage during a run."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Literal

from pydantic import BaseModel, Field

EventType = Literal[
    "VALIDATE_START",
    "VALIDATE_OK",
    "VALIDATE_FAIL",
    "AUTH_START",
    "AUTH_OK",
    "AUTH_WAITING",
    "AUTH_FAIL",
    "EXECUTE_START",
    "STEP_START",
    "STEP_OK",
    "STEP_FAIL",
    "STEP_HEALED",
    "STEP_SKIP",
    "EXECUTE_DONE",
    "LLM_CALL_START",
    "LLM_CALL_OK",
    "LLM_CALL_FAIL",
    "LLM_CALL_FALLBACK",
    "CLASSIFY_START",
    "CLASSIFY_OK",
    "CLASSIFY_FAIL",
    "REPORT_READY",
    "PIPELINE_ABORT",
]

StageName = Literal["VALIDATE", "AUTH", "EXECUTE", "CLASSIFY", "REPORT", "PIPELINE"]


class HealthEvent(BaseModel):
    run_id: str
    stage: StageName
    event_type: EventType
    seq: int
    timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    message: str
    details: dict[str, Any] = Field(default_factory=dict)

    model_config = {"frozen": True}


class EventBus:
    """In-process event accumulator for one run."""

    def __init__(self, run_id: str) -> None:
        self.run_id = run_id
        self._seq = 0
        self._events: list[HealthEvent] = []

    def emit(
        self,
        stage: StageName,
        event_type: EventType,
        message: str,
        details: dict[str, Any] | None = None,
    ) -> HealthEvent:
        self._seq += 1
        ev = HealthEvent(
            run_id=self.run_id,
            stage=stage,
            event_type=event_type,
            seq=self._seq,
            message=message,
            details=details or {},
        )
        self._events.append(ev)
        return ev

    @property
    def events(self) -> list[HealthEvent]:
        return list(self._events)
