"""Tests for engine/qa_context.py."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from blop.schemas import QAContext


@pytest.mark.asyncio
async def test_bottom_heavy_pyramid_detected():
    from blop.engine.qa_context import build_qa_context

    flows = [
        {"flow_name": f"flow_{i}", "business_criticality": "other", "assertion_count": 0, "steps": []} for i in range(5)
    ]
    run_cases: list[dict] = []

    ctx = await build_qa_context(
        app_url="https://example.com",
        flows=flows,
        run_cases=run_cases,
    )

    assert isinstance(ctx, QAContext)
    assert ctx.test_pyramid.is_bottom_heavy is True
    assert ctx.test_pyramid.happy_path_count == 5


@pytest.mark.asyncio
async def test_coverage_gap_no_test():
    from blop.engine.qa_context import build_qa_context

    flows = [
        {"flow_name": "checkout", "business_criticality": "revenue", "assertion_count": 2, "steps": []},
        {"flow_name": "homepage", "business_criticality": "other", "assertion_count": 0, "steps": []},
    ]
    now = datetime.now(timezone.utc).isoformat()
    run_cases = [
        {"flow_name": "homepage", "status": "pass", "failure_reason": None, "created_at": now},
    ]

    ctx = await build_qa_context(
        app_url="https://example.com",
        flows=flows,
        run_cases=run_cases,
    )

    gap_names = [g.flow_name for g in ctx.coverage_gaps]
    assert "checkout" in gap_names

    checkout_gap = next(g for g in ctx.coverage_gaps if g.flow_name == "checkout")
    assert checkout_gap.gap_type == "no_test"
    assert checkout_gap.severity == "critical"


@pytest.mark.asyncio
async def test_flakiness_signal_detected():
    from blop.engine.qa_context import build_qa_context

    flows = [
        {"flow_name": "login", "business_criticality": "activation", "assertion_count": 1, "steps": []},
    ]
    now = datetime.now(timezone.utc)
    run_cases = []
    for i in range(10):
        status = "pass" if i % 2 == 0 else "fail"
        ts = (now - timedelta(hours=i)).isoformat()
        run_cases.append({"flow_name": "login", "status": status, "failure_reason": None, "created_at": ts})

    ctx = await build_qa_context(
        app_url="https://example.com",
        flows=flows,
        run_cases=run_cases,
    )

    flaky_names = [s.flow_name for s in ctx.flakiness_signals if s.is_flaky]
    assert "login" in flaky_names


@pytest.mark.asyncio
async def test_risk_matrix_revenue_flow_high_impact():
    from blop.engine.qa_context import build_qa_context

    flows = [
        {"flow_name": "payment", "business_criticality": "revenue", "assertion_count": 2, "steps": []},
    ]
    now = datetime.now(timezone.utc)
    run_cases = []
    for i in range(10):
        status = "pass" if i < 5 else "fail"
        ts = (now - timedelta(hours=i)).isoformat()
        run_cases.append({"flow_name": "payment", "status": status, "failure_reason": None, "created_at": ts})

    ctx = await build_qa_context(
        app_url="https://example.com",
        flows=flows,
        run_cases=run_cases,
    )

    assert len(ctx.risk_matrix) >= 1
    entry = next(e for e in ctx.risk_matrix if e.flow_name == "payment")
    assert entry.impact >= 0.8
    assert abs(entry.likelihood - 0.5) < 0.15


@pytest.mark.asyncio
async def test_defect_distribution_categorized():
    from blop.engine.qa_context import build_qa_context

    flows = [
        {"flow_name": "api_flow", "business_criticality": "other", "assertion_count": 0, "steps": []},
        {"flow_name": "slow_flow", "business_criticality": "other", "assertion_count": 0, "steps": []},
        {"flow_name": "assert_flow", "business_criticality": "other", "assertion_count": 1, "steps": []},
    ]
    now = datetime.now(timezone.utc).isoformat()
    run_cases = [
        {"flow_name": "api_flow", "status": "fail", "failure_reason": "api request failed with 500", "created_at": now},
        {
            "flow_name": "slow_flow",
            "status": "fail",
            "failure_reason": "timeout waiting for response",
            "created_at": now,
        },
        {
            "flow_name": "assert_flow",
            "status": "fail",
            "failure_reason": "assertion failed: text not found",
            "created_at": now,
        },
    ]

    ctx = await build_qa_context(
        app_url="https://example.com",
        flows=flows,
        run_cases=run_cases,
    )

    valid_keys = {"functional", "performance", "ui", "integration", "unknown"}
    assert set(ctx.defect_distribution.keys()).issubset(valid_keys)
    total = sum(ctx.defect_distribution.values())
    assert total > 0
