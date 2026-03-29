"""Optional Prometheus metrics (install blop-mcp[server] + prometheus-client)."""

from __future__ import annotations

from typing import Any

_LOADED: dict[str, Any] | None = None


def _ensure() -> dict[str, Any] | bool | None:
    global _LOADED
    if _LOADED is not None:
        return _LOADED
    try:
        from prometheus_client import Counter, Gauge, Histogram, generate_latest

        runs = Counter(
            "blop_runs_total",
            "Runs reaching a terminal status",
            ["status"],
        )
        llm = Counter(
            "blop_llm_calls_total",
            "Successful LLM invocations via ainvoke_llm",
            ["provider", "tool"],
        )
        duration = Histogram(
            "blop_run_duration_seconds",
            "Wall time from run started_at to terminal update",
            ["status"],
            buckets=(1.0, 5.0, 30.0, 60.0, 300.0, 900.0, 3600.0, 7200.0),
        )
        active = Gauge(
            "blop_active_runs",
            "Runs created in this process not yet observed as terminal (best-effort)",
        )
        _LOADED = {
            "runs": runs,
            "llm": llm,
            "duration": duration,
            "active": active,
            "generate_latest": generate_latest,
        }
    except ImportError:
        _LOADED = False
    return _LOADED


def inc_active_run() -> None:
    st = _ensure()
    if not st or st is False:
        return
    st["active"].inc()


def record_run_terminal(
    *,
    status: str,
    duration_seconds: float | None = None,
    already_terminal: bool = False,
) -> None:
    st = _ensure()
    if not st or st is False:
        return
    if already_terminal:
        return
    st["runs"].labels(status=status).inc()
    if duration_seconds is not None and duration_seconds >= 0:
        st["duration"].labels(status=status).observe(duration_seconds)
    st["active"].dec()


def inc_llm_call(*, provider: str, tool: str) -> None:
    st = _ensure()
    if not st or st is False:
        return
    st["llm"].labels(provider=provider, tool=tool).inc()


def metrics_text() -> str | None:
    st = _ensure()
    if not st or st is False:
        return None
    gen = st["generate_latest"]
    return gen().decode("utf-8")
