"""Canonical activity names for run_health_events → process mining (PM4Py).

All ``concept:name`` values should be stable, lowercase snake segments, no spaces.
See ``docs/process_event_log.md`` for the full table.
"""

from __future__ import annotations

import re
from typing import Any

_SEGMENT_RE = re.compile(r"[^a-z0-9]+")


def _segment(s: str | None, *, fallback: str = "unknown") -> str:
    if not s or not isinstance(s, str):
        return fallback
    t = _SEGMENT_RE.sub("_", s.strip().lower())
    t = t.strip("_")
    return t or fallback


def canonical_replay_step_activity(action: str | None, status: str | None) -> str:
    """Activity for ``replay_step_completed`` payloads: ``replay_{action}_{status}``."""
    a = _segment(action, fallback="step")
    st = _segment(status, fallback="unknown")
    return f"replay_{a}_{st}"


# event_type → concept:name when payload does not override (run/case lifecycle)
CANONICAL_ACTIVITY_BY_EVENT_TYPE: dict[str, str] = {
    "run_startup_timing": "run_startup_timing",
    "run_started": "run_started",
    "run_completed": "run_completed",
    "run_failed": "run_failed",
    "run_cancelled": "run_cancelled",
    "run_checkpointed": "run_checkpointed",
    "run_resumed": "run_resumed",
    "run_waiting_auth": "run_waiting_auth",
    "run_force_terminated": "run_force_terminated",
    "auth_landing_observed": "auth_landing_observed",
    "case_completed": "case_completed",
    "replay_step_completed": "replay_step_completed",  # superseded by payload activity
}


def canonical_activity_for_event(event_type: str, payload: dict[str, Any] | None) -> str:
    """Resolve normalized ``concept:name`` for a health event row."""
    et = _segment(event_type, fallback="unknown_event")
    payload = payload or {}

    if et == "replay_step_completed":
        explicit = payload.get("activity")
        if isinstance(explicit, str) and explicit.strip():
            return _normalize_explicit_activity(explicit.strip())
        return canonical_replay_step_activity(
            str(payload.get("action") or ""),
            str(payload.get("status") or ""),
        )

    if et == "auth_landing_observed":
        return "auth_landing_observed"

    return CANONICAL_ACTIVITY_BY_EVENT_TYPE.get(et, et)


def _normalize_explicit_activity(raw: str) -> str:
    """Keep payload.activity if already canonical; otherwise sanitize."""
    if re.match(r"^[a-z][a-z0-9_]*$", raw) and raw.count("__") == 0:
        return raw
    return _segment(raw, fallback="activity")
