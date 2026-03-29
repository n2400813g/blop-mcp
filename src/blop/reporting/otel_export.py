"""OTLP-shaped JSON export for a Blop run (traces from health events + run metadata).

No network I/O — produces a serializable dict suitable for piping to an OpenTelemetry collector.
"""

from __future__ import annotations

import time
from typing import Any

from blop.engine.errors import BLOP_RUN_NOT_FOUND, tool_error
from blop.storage import sqlite


def _iso_to_nanos(iso_ts: str) -> int:
    """Best-effort parse ISO timestamp to Unix nanoseconds for OTLP."""
    try:
        from datetime import datetime

        s = iso_ts.strip()
        if s.endswith("Z"):
            s = s[:-1] + "+00:00"
        dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
        return int(dt.timestamp() * 1_000_000_000)
    except Exception:
        return int(time.time() * 1_000_000_000)


def _str_attr(key: str, value: str) -> dict[str, Any]:
    return {"key": key, "value": {"stringValue": value}}


def _int_attr(key: str, value: int) -> dict[str, Any]:
    return {"key": key, "value": {"intValue": str(value)}}


async def build_otel_run_trace_export(run_id: str, *, health_limit: int = 2000) -> dict[str, Any]:
    """Return OTLP JSON-like resourceSpans for one run (read-only, local SQLite)."""
    run = await sqlite.get_run(run_id)
    if not run:
        return tool_error(f"run_not_found:{run_id}", BLOP_RUN_NOT_FOUND, details={"run_id": run_id})

    events = await sqlite.list_run_health_events(run_id, limit=health_limit)
    resource_attrs = [
        _str_attr("service.name", "blop-mcp"),
        _str_attr("blop.run_id", run_id),
    ]
    app_url = run.get("app_url")
    if isinstance(app_url, str) and app_url:
        resource_attrs.append(_str_attr("blop.app_url", app_url))

    spans: list[dict[str, Any]] = []
    for idx, ev in enumerate(events):
        et = str(ev.get("event_type") or "unknown")
        payload = ev.get("payload") if isinstance(ev.get("payload"), dict) else {}
        ts = str(ev.get("created_at") or "")
        start_nanos = _iso_to_nanos(ts)
        span_id = f"{idx:016x}"[-16:]
        attrs = [
            _str_attr("blop.event_type", et),
        ]
        if payload:
            for pk in ("case_id", "flow_id", "status", "reason", "activity", "action"):
                if pk in payload and payload[pk] is not None:
                    attrs.append(_str_attr(f"blop.payload.{pk}", str(payload[pk])))
        spans.append(
            {
                "traceId": run_id[:32].ljust(32, "0")[:32],
                "spanId": span_id,
                "name": et,
                "kind": "SPAN_KIND_INTERNAL",
                "startTimeUnixNano": str(start_nanos),
                "endTimeUnixNano": str(start_nanos + 1),
                "attributes": attrs,
            }
        )

    return {
        "resourceSpans": [
            {
                "resource": {
                    "attributes": resource_attrs,
                },
                "scopeSpans": [
                    {
                        "scope": {"name": "blop.run", "version": "1"},
                        "spans": spans,
                    }
                ],
            }
        ],
        "blop_meta": {
            "run_id": run_id,
            "run_status": run.get("status"),
            "span_count": len(spans),
        },
    }
