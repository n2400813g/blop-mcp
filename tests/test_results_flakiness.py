"""Tests for flakiness leaderboard and defect distribution in get_risk_analytics helpers."""

from __future__ import annotations

from blop.tools.results import _categorize_failure_reason, _compute_flow_flakiness


def test_flakiness_leaderboard_computed_from_alternating_results():
    cases = [{"flow_name": "login", "status": "passed" if i % 2 == 0 else "failed"} for i in range(6)]
    result = _compute_flow_flakiness(cases)
    login_entry = next((r for r in result if r["flow_name"] == "login"), None)
    assert login_entry is not None, "Expected 'login' entry in flakiness result"
    assert login_entry["is_flaky"] is True
    assert login_entry["cv"] > 0.3


def test_flakiness_leaderboard_stable_flow_not_flaky():
    cases = [{"flow_name": "dashboard", "status": "passed"} for _ in range(8)]
    result = _compute_flow_flakiness(cases)
    dashboard_entry = next((r for r in result if r["flow_name"] == "dashboard"), None)
    assert dashboard_entry is not None, "Expected 'dashboard' entry in flakiness result"
    assert dashboard_entry["is_flaky"] is False


def test_defect_distribution_categorization():
    assert _categorize_failure_reason("API returned 500 status") == "integration"
    assert _categorize_failure_reason("element not found in screenshot") == "ui"
    assert _categorize_failure_reason("timeout waiting for page") == "performance"
    assert _categorize_failure_reason("assertion failed: checkout total wrong") == "functional"
    assert _categorize_failure_reason("reflected XSS in search query") == "security"
