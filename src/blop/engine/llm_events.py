"""Context-variable-based LLM_CALL_* event emission for pipeline runs.

Usage:
    token = set_llm_event_bus(ctx.bus)
    try:
        # ... pipeline stages run here ...
    finally:
        llm_event_bus.reset(token)

Any call to emit_llm_* within that context will attach events to the bus.
Safe to call when no bus is set — all functions are no-ops.
"""

from __future__ import annotations

import uuid
from contextvars import ContextVar
from typing import Optional

from blop.engine.events import EventBus

llm_event_bus: ContextVar[Optional[EventBus]] = ContextVar("llm_event_bus", default=None)


def set_llm_event_bus(bus: EventBus):
    """Activate bus for LLM events in the current async context. Returns reset token."""
    return llm_event_bus.set(bus)


def _bus() -> EventBus | None:
    return llm_event_bus.get(None)


def emit_llm_start(*, provider: str, model: str, call_id: str | None = None) -> None:
    bus = _bus()
    if bus is None:
        return
    bus.emit(
        "PIPELINE",
        "LLM_CALL_START",
        f"LLM call started: {provider}/{model}",
        {"provider": provider, "model": model, "call_id": call_id or str(uuid.uuid4())},
    )


def emit_llm_ok(*, provider: str, model: str, call_id: str | None = None) -> None:
    bus = _bus()
    if bus is None:
        return
    bus.emit(
        "PIPELINE",
        "LLM_CALL_OK",
        f"LLM call succeeded: {provider}/{model}",
        {"provider": provider, "model": model, "call_id": call_id or ""},
    )


def emit_llm_fail(*, provider: str, model: str, call_id: str | None = None, error: str) -> None:
    bus = _bus()
    if bus is None:
        return
    bus.emit(
        "PIPELINE",
        "LLM_CALL_FAIL",
        f"LLM call failed: {provider}/{model} — {error}",
        {"provider": provider, "model": model, "call_id": call_id or "", "error": error},
    )


def emit_llm_fallback(*, from_provider: str, to_provider: str, reason: str) -> None:
    bus = _bus()
    if bus is None:
        return
    bus.emit(
        "PIPELINE",
        "LLM_CALL_FALLBACK",
        f"LLM fallback: {from_provider} → {to_provider} ({reason})",
        {"from_provider": from_provider, "to_provider": to_provider, "reason": reason},
    )
