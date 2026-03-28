"""Offline smoke checks for orchestration helpers (BrowserGym-inspired eval hook).

Run: pytest tests/eval_harness/test_orchestration_smoke.py -m eval_harness
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from blop.engine.regression import _aria_consistency, _selector_entropy
from blop.schemas import FlowStep, IntentContract, RecordedFlow

pytestmark = pytest.mark.eval_harness


def test_selector_entropy_heuristic_stable():
    assert _selector_entropy(None) == 1.0
    assert _selector_entropy("#id") < _selector_entropy("div > div > span:nth-child(2)")


def test_aria_consistency_scores_semantics():
    step = FlowStep(
        step_id=0,
        action="click",
        aria_role="button",
        aria_name="Submit",
        label_text=None,
        testid_selector="[data-testid='x']",
    )
    assert _aria_consistency(step) > 0.5


def test_make_flow_minimal_recorded_flow():
    rf = RecordedFlow(
        flow_id="f1",
        flow_name="smoke",
        app_url="https://example.com",
        goal="smoke",
        steps=[
            FlowStep(step_id=0, action="navigate", value="https://example.com"),
            FlowStep(step_id=1, action="click", selector="button"),
        ],
        created_at=datetime.now(timezone.utc).isoformat(),
        intent_contract=IntentContract(
            goal_text="smoke",
            goal_type="milestone",
            target_surface="public_site",
            success_assertions=[],
            must_interact=[],
            forbidden_shortcuts=[],
            scope="public",
            business_criticality="other",
            planning_source="explicit_goal",
            expected_url_patterns=["https://example.com"],
            allowed_fallbacks=["hybrid_repair"],
        ),
    )
    assert len(rf.steps) == 2
