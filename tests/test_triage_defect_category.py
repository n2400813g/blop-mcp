"""Tests for defect_category and flakiness_context in triage_release_blocker."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from blop.engine.defect_classifier import categorize_failure_reason


def test_categorize_failure_reason_api_error():
    assert categorize_failure_reason("API call failed with 503") == "integration"
    assert categorize_failure_reason("HTTP request returned 500 status") == "integration"
    assert categorize_failure_reason("network request to /api/users failed") == "integration"


def test_categorize_failure_reason_unknown_maps_to_functional():
    assert categorize_failure_reason(None) == "functional"
    assert categorize_failure_reason("") == "functional"
    assert categorize_failure_reason("unexpected behavior") == "functional"


@pytest.mark.asyncio
async def test_defect_category_in_triage_output():
    from blop.schemas import FailureCase
    from blop.tools.triage import triage_release_blocker

    failed_case = FailureCase(
        run_id="run-triage-1",
        flow_id="flow-1",
        flow_name="checkout",
        status="fail",
        severity="blocker",
        business_criticality="revenue",
        raw_result="API returned 500 status on checkout submission",
    )

    run = {
        "run_id": "run-triage-1",
        "status": "failed",
        "app_url": "https://example.com",
        "profile_name": None,
    }

    with patch("blop.storage.sqlite.get_run", new_callable=AsyncMock, return_value=run):
        with patch(
            "blop.storage.sqlite.list_cases_for_run",
            new_callable=AsyncMock,
            return_value=[failed_case],
        ):
            with patch(
                "blop.storage.sqlite.list_artifacts_for_run",
                new_callable=AsyncMock,
                return_value=[],
            ):
                with patch(
                    "blop.storage.sqlite.get_latest_context_graph",
                    new_callable=AsyncMock,
                    return_value=None,
                ):
                    with patch(
                        "blop.storage.sqlite.list_cases_for_flow",
                        new_callable=AsyncMock,
                        return_value=[],
                    ):
                        out = await triage_release_blocker(run_id="run-triage-1")

    assert "defect_category" in out
    assert "flakiness_context" in out
    assert out["defect_category"] == "integration"
    fc = out["flakiness_context"]
    assert isinstance(fc, dict)
    assert "flow_name" in fc
    assert "recent_pass_count" in fc
    assert "recent_fail_count" in fc
    assert "is_known_flaky" in fc
