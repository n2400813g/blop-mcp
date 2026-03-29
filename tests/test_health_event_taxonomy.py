"""Tests for reporting/health_event_taxonomy.py."""

from __future__ import annotations

from blop.reporting.health_event_taxonomy import (
    canonical_activity_for_event,
    canonical_replay_step_activity,
)


def test_canonical_replay_step_activity_normalizes():
    assert canonical_replay_step_activity("click", "pass") == "replay_click_pass"
    assert canonical_replay_step_activity("Navigate", "FAIL") == "replay_navigate_fail"


def test_canonical_activity_for_replay_payload():
    act = canonical_activity_for_event(
        "replay_step_completed",
        {"case_id": "c1", "action": "fill", "status": "pass", "activity": "replay_fill_pass"},
    )
    assert act == "replay_fill_pass"


def test_run_level_event_types():
    assert canonical_activity_for_event("run_started", {}) == "run_started"
    assert canonical_activity_for_event("run_completed", {}) == "run_completed"
    assert canonical_activity_for_event("case_completed", {"case_id": "x"}) == "case_completed"


def test_auth_landing():
    assert canonical_activity_for_event("auth_landing_observed", {"case_id": "x"}) == "auth_landing_observed"
