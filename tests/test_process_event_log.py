"""Tests for reporting/process_event_log.py."""

from __future__ import annotations

from blop.reporting.process_event_log import (
    ProcessEventLogRow,
    health_record_to_rows,
    health_records_to_event_log,
)


def test_replay_step_maps_activity():
    rec = {
        "event_id": "e1",
        "run_id": "r1",
        "event_type": "replay_step_completed",
        "payload": {
            "case_id": "c1",
            "action": "click",
            "status": "pass",
            "activity": "replay_click_pass",
        },
        "created_at": "2025-03-01T12:00:00Z",
    }
    rows = health_record_to_rows(rec)
    assert len(rows) == 1
    assert rows[0].case_id == "c1"
    assert rows[0].activity == "replay_click_pass"
    assert rows[0].lifecycle == "replay"


def test_run_level_default_case():
    rec = {
        "event_id": "e2",
        "run_id": "r9",
        "event_type": "run_started",
        "payload": {"flow_count": 2},
        "created_at": "2025-03-01T12:00:01Z",
    }
    rows = health_record_to_rows(rec)
    assert rows[0].case_id == "run_scope::r9"
    assert rows[0].activity == "run_started"


def test_ordered_log():
    records = [
        {
            "event_type": "run_started",
            "run_id": "r",
            "payload": {},
            "created_at": "2025-03-01T12:00:00Z",
            "event_id": "a",
        },
        {
            "event_type": "replay_step_completed",
            "run_id": "r",
            "payload": {"case_id": "cx", "action": "navigate", "status": "pass", "activity": "replay_navigate_pass"},
            "created_at": "2025-03-01T12:00:01Z",
            "event_id": "b",
        },
    ]
    rows = health_records_to_event_log(records)
    assert len(rows) == 2
    assert isinstance(rows[0], ProcessEventLogRow)
