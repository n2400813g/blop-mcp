# tests/test_llm_events.py

from blop.engine.events import EventBus
from blop.engine.llm_events import (
    emit_llm_fail,
    emit_llm_fallback,
    emit_llm_ok,
    emit_llm_start,
    llm_event_bus,
    set_llm_event_bus,
)


def test_llm_events_emitted_when_bus_set():
    bus = EventBus("run_llm_01")
    token = set_llm_event_bus(bus)
    try:
        emit_llm_start(provider="google", model="gemini-2.5-flash", call_id="c1")
        emit_llm_ok(provider="google", model="gemini-2.5-flash", call_id="c1")
    finally:
        llm_event_bus.reset(token)

    events = bus.events
    assert any(e.event_type == "LLM_CALL_START" for e in events)
    assert any(e.event_type == "LLM_CALL_OK" for e in events)
    start = next(e for e in events if e.event_type == "LLM_CALL_START")
    assert start.details["provider"] == "google"
    assert start.details["model"] == "gemini-2.5-flash"
    assert start.details["call_id"] == "c1"


def test_llm_events_noop_when_no_bus_set():
    # Must not raise even with no bus active
    emit_llm_start(provider="google", model="gemini-2.5-flash", call_id="c2")
    emit_llm_fail(provider="google", model="gemini-2.5-flash", call_id="c2", error="quota exceeded")


def test_llm_fail_event_has_error_field():
    bus = EventBus("run_llm_02")
    token = set_llm_event_bus(bus)
    try:
        emit_llm_fail(provider="anthropic", model="claude-sonnet-4-6", call_id="c3", error="rate limit")
    finally:
        llm_event_bus.reset(token)
    fail_events = [e for e in bus.events if e.event_type == "LLM_CALL_FAIL"]
    assert len(fail_events) == 1
    assert fail_events[0].details["error"] == "rate limit"
    assert fail_events[0].details["provider"] == "anthropic"


def test_llm_fallback_event():
    bus = EventBus("run_llm_03")
    token = set_llm_event_bus(bus)
    try:
        emit_llm_fallback(from_provider="google", to_provider="anthropic", reason="quota exceeded")
    finally:
        llm_event_bus.reset(token)
    fb = [e for e in bus.events if e.event_type == "LLM_CALL_FALLBACK"]
    assert len(fb) == 1
    assert fb[0].details["from_provider"] == "google"
    assert fb[0].details["to_provider"] == "anthropic"
