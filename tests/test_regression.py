"""Tests for engine/regression.py."""
from __future__ import annotations

import asyncio
import os
import sys
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from blop.schemas import FlowStep, IntentContract, RecordedFlow


def make_flow(flow_id: str = "flow1", goal: str = "Test the page") -> RecordedFlow:
    return RecordedFlow(
        flow_id=flow_id,
        flow_name="test_flow",
        app_url="https://example.com",
        goal=goal,
        steps=[
            FlowStep(step_id=0, action="navigate", value="https://example.com"),
            FlowStep(step_id=1, action="assert", description="page loads"),
        ],
        created_at=datetime.now(timezone.utc).isoformat(),
        intent_contract=IntentContract(
            goal_text=goal,
            goal_type="milestone",
            target_surface="authenticated_app",
            success_assertions=["page loads"],
            must_interact=["navigate"],
            forbidden_shortcuts=["goal_fallback_without_surface_match"],
            scope="authed",
            business_criticality="other",
            planning_source="explicit_goal",
            expected_url_patterns=["https://example.com"],
            allowed_fallbacks=["hybrid_repair"],
        ),
    )


def _mock_browser_use():
    """Create a mock browser_use module that avoids display detection."""
    mock_bu = MagicMock()
    mock_bu.Agent = MagicMock()
    mock_bu.BrowserSession = MagicMock()
    mock_bu.llm.ChatGoogle = MagicMock()
    mock_bu.llm.messages.UserMessage = MagicMock()
    return mock_bu


class _FinalPage:
    async def inner_text(self, selector: str) -> str:
        return "All good"


@pytest.mark.asyncio
async def test_execute_flow_pass():
    """Flow returns pass status when result has no error keywords."""
    from blop.engine.regression import execute_flow

    mock_history = MagicMock()
    mock_history.final_result.return_value = "All steps completed successfully"

    mock_agent = AsyncMock()
    mock_agent.run.return_value = mock_history

    mock_session = AsyncMock()
    mock_session.context = None
    mock_session.aclose = AsyncMock()
    mock_session.get_current_page = AsyncMock(return_value=_FinalPage())
    mock_session.get_current_page_url = AsyncMock(return_value="https://example.com")

    mock_bu = _mock_browser_use()
    mock_bu.Agent.return_value = mock_agent
    mock_bu.BrowserSession.return_value = mock_session

    with patch.dict(os.environ, {"GOOGLE_API_KEY": "test_key"}):
        with patch.dict(sys.modules, {"browser_use": mock_bu, "browser_use.llm": mock_bu.llm, "browser_use.llm.messages": mock_bu.llm.messages}):
            with patch("blop.engine.browser.make_browser_profile"):
                with patch("blop.storage.files.screenshot_path", return_value="/tmp/shot.png"):
                    flow = make_flow()
                    case = await execute_flow(
                        flow=flow,
                        app_url="https://example.com",
                        run_id="run1",
                        case_id="case1",
                        storage_state=None,
                        headless=True,
                        run_mode="goal_fallback",
                    )

    assert case.status == "pass"
    assert case.flow_id == "flow1"
    assert case.drift_summary.drift_detected is True
    assert "plan_drift" in case.drift_summary.drift_types


@pytest.mark.asyncio
async def test_execute_flow_fail_on_error_keyword():
    """Flow returns fail status when result contains error keywords."""
    from blop.engine.regression import execute_flow

    mock_history = MagicMock()
    mock_history.final_result.return_value = "Page returned 404 error"

    mock_agent = AsyncMock()
    mock_agent.run.return_value = mock_history

    mock_session = AsyncMock()
    mock_session.context = None
    mock_session.aclose = AsyncMock()
    mock_session.get_current_page = AsyncMock(return_value=_FinalPage())
    mock_session.get_current_page_url = AsyncMock(return_value="https://example.com")

    mock_bu = _mock_browser_use()
    mock_bu.Agent.return_value = mock_agent
    mock_bu.BrowserSession.return_value = mock_session

    with patch.dict(os.environ, {"GOOGLE_API_KEY": "test_key"}):
        with patch.dict(sys.modules, {"browser_use": mock_bu, "browser_use.llm": mock_bu.llm, "browser_use.llm.messages": mock_bu.llm.messages}):
            with patch("blop.engine.browser.make_browser_profile"):
                with patch("blop.storage.files.screenshot_path", return_value="/tmp/shot.png"):
                    flow = make_flow()
                    case = await execute_flow(
                        flow=flow,
                        app_url="https://example.com",
                        run_id="run1",
                        case_id="case1",
                        storage_state=None,
                        headless=True,
                        run_mode="goal_fallback",
                    )

    assert case.status == "fail"


def test_build_drift_summary_flags_goal_fallback_when_not_allowed():
    from blop.engine.regression import _build_drift_summary

    flow = make_flow(goal="Open settings")
    drift = _build_drift_summary(
        flow=flow,
        status="pass",
        replay_mode="goal_fallback",
        assertion_results=[],
        failure_reason_codes=[],
        rerecorded=False,
        actual_landing_url="https://example.com/settings",
    )

    assert drift.drift_detected is True
    assert "plan_drift" in drift.drift_types
    assert "goal_fallback" in drift.disallowed_fallback_used


def test_build_drift_summary_treats_public_routes_as_public_surface():
    from blop.engine.regression import _build_drift_summary

    flow = make_flow(goal="Open public docs")
    flow.intent_contract.target_surface = "public_site"
    flow.intent_contract.scope = "public"

    drift = _build_drift_summary(
        flow=flow,
        status="pass",
        replay_mode="strict_steps",
        assertion_results=[],
        failure_reason_codes=[],
        rerecorded=False,
        actual_landing_url="https://testpages.eviltester.com/pages/input-elements/text-inputs/",
    )

    assert drift.actual_surface == "public_site"
    assert drift.surface_match is True
    assert "surface_drift" not in drift.drift_types


def test_selector_heal_thresholds_are_stricter_for_high_risk_steps():
    from blop.engine.regression import _should_auto_heal

    assert _should_auto_heal(0.84, 0.1, action="click", selector_entropy=0.1) is True
    assert _should_auto_heal(0.8, 0.2, action="fill", selector_entropy=0.6) is False
    assert _should_auto_heal(0.93, 0.16, action="drag", selector_entropy=0.2) is False


def test_interleave_flows_by_entry_area_round_robins_sections():
    from blop.engine.regression import _interleave_flows_by_entry_area

    billing_a = make_flow("billing-a", "Upgrade billing")
    billing_a.entry_url = "https://example.com/billing/upgrade"
    settings = make_flow("settings", "Update profile")
    settings.entry_url = "https://example.com/settings/profile"
    billing_b = make_flow("billing-b", "Review invoices")
    billing_b.entry_url = "https://example.com/billing/invoices"

    ordered = _interleave_flows_by_entry_area([billing_a, billing_b, settings])

    assert [flow.flow_id for flow in ordered] == ["billing-a", "settings", "billing-b"]


@pytest.mark.asyncio
async def test_run_flows_parallel():
    """run_flows executes multiple flows and returns one case per flow."""
    from blop.engine.regression import run_flows

    mock_history = MagicMock()
    mock_history.final_result.return_value = "Success"

    mock_agent = AsyncMock()
    mock_agent.run.return_value = mock_history

    mock_session = AsyncMock()
    mock_session.context = None
    mock_session.aclose = AsyncMock()
    mock_session.get_current_page = AsyncMock(return_value=_FinalPage())
    mock_session.get_current_page_url = AsyncMock(return_value="https://example.com")

    mock_bu = _mock_browser_use()
    mock_bu.Agent.return_value = mock_agent
    mock_bu.BrowserSession.return_value = mock_session

    flows = [make_flow(f"flow{i}", f"Goal {i}") for i in range(3)]

    with patch.dict(os.environ, {"GOOGLE_API_KEY": "test_key"}):
        with patch.dict(sys.modules, {"browser_use": mock_bu, "browser_use.llm": mock_bu.llm, "browser_use.llm.messages": mock_bu.llm.messages}):
            with patch("blop.engine.browser.make_browser_profile"):
                with patch("blop.storage.files.screenshot_path", return_value="/tmp/shot.png"):
                    cases = await run_flows(
                        flows=flows,
                        app_url="https://example.com",
                        run_id="run1",
                        storage_state=None,
                        headless=True,
                    )

    assert len(cases) == 3
    assert all(c.run_id == "run1" for c in cases)


@pytest.mark.asyncio
async def test_run_flows_propagates_business_criticality():
    """Flow with business_criticality='revenue' -> resulting FailureCase has same value."""
    from blop.engine.regression import run_flows
    from blop.schemas import RecordedFlow

    revenue_flow = RecordedFlow(
        flow_id="flow-rev",
        flow_name="checkout_with_credit_card",
        app_url="https://example.com",
        goal="Complete checkout",
        steps=[FlowStep(step_id=0, action="navigate", value="https://example.com/checkout")],
        created_at=datetime.now(timezone.utc).isoformat(),
        business_criticality="revenue",
    )

    mock_history = MagicMock()
    mock_history.final_result.return_value = "Checkout completed successfully"

    mock_agent = AsyncMock()
    mock_agent.run.return_value = mock_history

    mock_session = AsyncMock()
    mock_session.context = None
    mock_session.aclose = AsyncMock()
    mock_session.get_current_page = AsyncMock(return_value=_FinalPage())
    mock_session.get_current_page_url = AsyncMock(return_value="https://example.com")

    mock_bu = _mock_browser_use()
    mock_bu.Agent.return_value = mock_agent
    mock_bu.BrowserSession.return_value = mock_session

    with patch.dict(os.environ, {"GOOGLE_API_KEY": "test_key"}):
        with patch.dict(sys.modules, {"browser_use": mock_bu, "browser_use.llm": mock_bu.llm, "browser_use.llm.messages": mock_bu.llm.messages}):
            with patch("blop.engine.browser.make_browser_profile"):
                with patch("blop.storage.files.screenshot_path", return_value="/tmp/shot.png"):
                    cases = await run_flows(
                        flows=[revenue_flow],
                        app_url="https://example.com",
                        run_id="run-rev",
                        storage_state=None,
                        headless=True,
                    )

    assert len(cases) == 1
    assert cases[0].flow_id == "flow-rev"
    assert cases[0].flow_name == "checkout_with_credit_card"


@pytest.mark.asyncio
async def test_run_flows_semaphore():
    """run_flows respects semaphore and does not exceed 5 concurrent flows."""
    from blop.engine.regression import run_flows

    concurrent_count = 0
    max_concurrent = 0

    async def slow_execute(*args, **kwargs):
        nonlocal concurrent_count, max_concurrent
        concurrent_count += 1
        max_concurrent = max(max_concurrent, concurrent_count)
        await asyncio.sleep(0.05)
        concurrent_count -= 1

        from blop.schemas import FailureCase
        return FailureCase(
            run_id=kwargs.get("run_id", "run1"),
            flow_id=kwargs.get("flow", make_flow()).flow_id,
            flow_name="test",
            status="pass",
        )

    flows = [make_flow(f"flow{i}") for i in range(10)]

    with patch("blop.engine.regression.execute_flow", side_effect=slow_execute):
        cases = await run_flows(
            flows=flows,
            app_url="https://example.com",
            run_id="run1",
            storage_state=None,
            headless=True,
        )

    assert len(cases) == 10
    assert max_concurrent <= 5


@pytest.mark.asyncio
async def test_run_flows_respects_replay_concurrency_override(monkeypatch):
    from blop.engine.regression import run_flows

    concurrent_count = 0
    max_concurrent = 0

    async def slow_execute(*args, **kwargs):
        nonlocal concurrent_count, max_concurrent
        concurrent_count += 1
        max_concurrent = max(max_concurrent, concurrent_count)
        await asyncio.sleep(0.05)
        concurrent_count -= 1

        from blop.schemas import FailureCase
        return FailureCase(
            run_id=kwargs.get("run_id", "run1"),
            flow_id=kwargs.get("flow", make_flow()).flow_id,
            flow_name="test",
            status="pass",
        )

    monkeypatch.setattr("blop.engine.regression.BLOP_REPLAY_CONCURRENCY", 2)
    flows = [make_flow(f"flow{i}") for i in range(6)]

    with patch("blop.engine.regression.execute_flow", side_effect=slow_execute):
        cases = await run_flows(
            flows=flows,
            app_url="https://example.com",
            run_id="run1",
            storage_state=None,
            headless=True,
        )

    assert len(cases) == 6
    assert max_concurrent <= 2


def test_repair_flow_context_suffix_includes_goal_for_weak_steps():
    from blop.engine.regression import _repair_flow_context_suffix

    step = FlowStep(step_id=1, action="click", target_text="click")

    suffix = _repair_flow_context_suffix(
        flow_name="view_plans_modal",
        flow_goal="Open the View Plans button until the upgrade plan modal is visible.",
        step_index=2,
        step=step,
    )

    assert "Flow name: view_plans_modal" in suffix
    assert "Flow goal: Open the View Plans button" in suffix
    assert "weak locator data" in suffix


@pytest.mark.asyncio
async def test_execute_recorded_flow_invalidates_cached_auth_on_blocked_result():
    from blop.engine.regression import execute_recorded_flow
    from blop.schemas import FailureCase

    flow = make_flow()
    flow.steps = []

    mock_page = AsyncMock()
    mock_page.on = MagicMock()
    mock_page.screenshot = AsyncMock()
    mock_context = AsyncMock()
    mock_context.new_page = AsyncMock(return_value=mock_page)
    mock_browser = AsyncMock()
    mock_browser.new_context = AsyncMock(return_value=mock_context)

    mock_playwright = AsyncMock()
    mock_playwright.__aenter__ = AsyncMock(return_value=mock_playwright)
    mock_playwright.__aexit__ = AsyncMock(return_value=False)
    mock_playwright.chromium.launch = AsyncMock(return_value=mock_browser)

    blocked_case = FailureCase(
        case_id="case-1",
        run_id="run-1",
        flow_id=flow.flow_id,
        flow_name=flow.flow_name,
        status="blocked",
    )

    with patch("playwright.async_api.async_playwright", return_value=mock_playwright):
        with patch("blop.engine.browser.make_browser_profile"):
            with patch("blop.engine.regression._trace_to_failure_case", return_value=blocked_case):
                with patch("blop.engine.auth.invalidate_validated_session_cache") as invalidate_cache:
                    result = await execute_recorded_flow(
                        flow=flow,
                        run_id="run-1",
                        case_id="case-1",
                        storage_state="/tmp/auth.json",
                        headless=True,
                    )

    assert result.status == "blocked"
    invalidate_cache.assert_called_once_with(storage_state_path="/tmp/auth.json")
