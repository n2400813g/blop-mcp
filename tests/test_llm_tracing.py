"""Tests for optional LLM OpenTelemetry wrapper."""

from __future__ import annotations

from blop.engine.llm_tracing import trace_llm_call


def test_trace_llm_call_noop_when_disabled(monkeypatch):
    monkeypatch.delenv("BLOP_OTEL_TRACING", raising=False)
    with trace_llm_call("test.span", {"k": "v"}):
        assert True
