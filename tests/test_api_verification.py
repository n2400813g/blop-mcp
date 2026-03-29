from __future__ import annotations

from datetime import datetime, timezone

from blop.engine.api_verification import evaluate_api_expectations
from blop.schemas import ApiExpectation, FlowStep, RecordedFlow


def test_api_verification_marks_required_expectation_failures():
    flow = RecordedFlow(
        flow_name="checkout",
        app_url="https://example.com",
        goal="Complete checkout",
        steps=[FlowStep(step_id=0, action="navigate", value="https://example.com/checkout")],
        created_at=datetime.now(timezone.utc).isoformat(),
        api_expectations=[ApiExpectation(name="checkout_api", url_contains="/api/checkout", methods=["POST"])],
    )

    results = evaluate_api_expectations(
        flow,
        [{"url": "https://example.com/api/checkout", "method": "POST", "status": 500}],
    )

    assert results[0]["passed"] is False
    assert results[0]["required"] is True
    assert results[0]["observed_statuses"] == [500]


def test_api_verification_keeps_optional_expectations_advisory():
    flow = RecordedFlow(
        flow_name="publish",
        app_url="https://example.com",
        goal="Publish article",
        steps=[FlowStep(step_id=0, action="navigate", value="https://example.com/editor")],
        created_at=datetime.now(timezone.utc).isoformat(),
        api_expectations=[
            ApiExpectation(name="publish_api", url_contains="/api/publish", methods=["POST"], required=False)
        ],
    )

    results = evaluate_api_expectations(flow, [])

    assert results[0]["passed"] is False
    assert results[0]["required"] is False
