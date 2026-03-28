"""Optional OpenTelemetry spans around LLM calls (no-op unless enabled + deps).

Set ``BLOP_OTEL_TRACING=1`` and install ``blop-mcp[otel]`` to emit spans to stderr (console).
Without the SDK extra, spans are skipped after a one-time import attempt.

Langfuse and other OTLP exporters can subscribe to the same tracer provider in a full deployment;
this module only spikes console export for local debugging.
"""

from __future__ import annotations

import os
from contextlib import contextmanager
from typing import Any, Iterator

_INIT_ATTEMPTED = False
_SDK_AVAILABLE = False


def _otel_tracing_enabled() -> bool:
    return os.getenv("BLOP_OTEL_TRACING", "").strip().lower() in ("1", "true", "yes", "on")


def _ensure_tracer_provider() -> None:
    global _INIT_ATTEMPTED, _SDK_AVAILABLE
    if _INIT_ATTEMPTED:
        return
    _INIT_ATTEMPTED = True
    try:
        from opentelemetry import trace
        from opentelemetry.sdk.trace import TracerProvider
        from opentelemetry.sdk.trace.export import ConsoleSpanExporter, SimpleSpanProcessor

        provider = TracerProvider()
        provider.add_span_processor(SimpleSpanProcessor(ConsoleSpanExporter()))
        trace.set_tracer_provider(provider)
        _SDK_AVAILABLE = True
    except ImportError:
        _SDK_AVAILABLE = False


def _str_attrs(attrs: dict[str, Any]) -> dict[str, str]:
    out: dict[str, str] = {}
    for k, v in attrs.items():
        if v is None:
            continue
        out[str(k)] = str(v)[:1200]
    return out


@contextmanager
def trace_llm_call(span_name: str, attrs: dict[str, Any] | None = None) -> Iterator[None]:
    """Sync context manager: active span around an ``ainvoke`` when OTel is enabled."""
    if not _otel_tracing_enabled():
        yield
        return
    _ensure_tracer_provider()
    if not _SDK_AVAILABLE:
        yield
        return
    try:
        from opentelemetry import trace

        tracer = trace.get_tracer("blop.llm", "0.1")
        with tracer.start_as_current_span(span_name, attributes=_str_attrs(attrs or {})):
            yield
    except Exception:
        yield
