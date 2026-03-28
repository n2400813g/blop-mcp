"""Process-mining oriented event log derived from run_health_events.

Maps control-plane health events to XES-style rows (case id, activity, timestamp)
for interchange with PM4Py and similar tools.
"""

from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Any, Literal

from pydantic import BaseModel, Field

from blop.reporting.health_event_taxonomy import canonical_activity_for_event
from blop.storage import sqlite


class ProcessEventLogRow(BaseModel):
    """One row in a process-oriented event log (concept:name / case id / time)."""

    case_id: str = Field(description="Process instance id (usually run_cases.case_id).")
    activity: str = Field(description="Normalized activity label (concept:name).")
    timestamp: str = Field(description="ISO-8601 timestamp (time:timestamp).")
    lifecycle: Literal["run", "case", "step", "replay", "unknown"] = "unknown"
    source_event_id: str | None = None
    source_event_type: str | None = None
    run_id: str | None = None
    attributes: dict[str, Any] = Field(default_factory=dict)


_RUN_SCOPE_PREFIX = "run_scope::"


def _parse_ts(created_at: str | None) -> str:
    if not created_at:
        return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    s = created_at.strip()
    if s.endswith("Z"):
        return s
    if re.match(r"^\d{4}-\d{2}-\d{2}", s) and "T" not in s:
        return s + "T00:00:00Z"
    return s if "T" in s else s + "Z"


def _lifecycle_for_event_type(event_type: str) -> Literal["run", "case", "step", "replay", "unknown"]:
    if event_type == "replay_step_completed":
        return "replay"
    if event_type in ("case_completed", "auth_landing_observed"):
        return "case"
    if event_type.startswith("run_") or event_type in ("run_startup_timing",):
        return "run"
    return "unknown"


def _default_case_id(run_id: str, payload: dict[str, Any]) -> str:
    cid = payload.get("case_id")
    if isinstance(cid, str) and cid.strip():
        return cid.strip()
    return f"{_RUN_SCOPE_PREFIX}{run_id}"


def health_record_to_rows(
    record: dict[str, Any],
) -> list[ProcessEventLogRow]:
    """Map one run_health_events row (dict with event_id, run_id, event_type, payload, created_at) to log rows."""
    event_type = str(record.get("event_type") or "")
    payload = record.get("payload") if isinstance(record.get("payload"), dict) else {}
    run_id = str(record.get("run_id") or "")
    event_id = record.get("event_id")
    created_at = record.get("created_at")
    ts = _parse_ts(created_at if isinstance(created_at, str) else None)
    case_id = _default_case_id(run_id, payload)
    activity = canonical_activity_for_event(event_type, payload)
    lifecycle = _lifecycle_for_event_type(event_type)
    attrs = {k: v for k, v in payload.items() if k != "case_id"}
    return [
        ProcessEventLogRow(
            case_id=case_id,
            activity=activity,
            timestamp=ts,
            lifecycle=lifecycle,
            source_event_id=str(event_id) if event_id else None,
            source_event_type=event_type or None,
            run_id=run_id or None,
            attributes=attrs,
        )
    ]


def health_records_to_event_log(records: list[dict[str, Any]]) -> list[ProcessEventLogRow]:
    """Convert ordered health event dicts to a flat process event log."""
    rows: list[ProcessEventLogRow] = []
    for rec in records:
        rows.extend(health_record_to_rows(rec))
    return rows


async def build_process_event_log_for_run(run_id: str, *, limit: int = 2000) -> list[ProcessEventLogRow]:
    """Load run health events from SQLite and build a process event log."""
    raw = await sqlite.list_run_health_events(run_id, limit=limit)
    return health_records_to_event_log(raw)


def event_log_to_csv_dicts(rows: list[ProcessEventLogRow]) -> list[dict[str, Any]]:
    """Flatten rows for CSV / pandas (PM4Py-friendly column names)."""
    out: list[dict[str, Any]] = []
    for r in rows:
        d = {
            "case:concept:name": r.case_id,
            "concept:name": r.activity,
            "time:timestamp": r.timestamp,
            "lifecycle": r.lifecycle,
        }
        if r.source_event_id:
            d["blop:event_id"] = r.source_event_id
        if r.run_id:
            d["blop:run_id"] = r.run_id
        for k, v in r.attributes.items():
            d[f"blop:attr:{k}"] = v
        out.append(d)
    return out
