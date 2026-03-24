from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import AsyncMock

import pytest

from blop.schemas import FailureCase, FlowStep, RecordedFlow, SiteInventory


def _inventory(routes: list[str]) -> SiteInventory:
    return SiteInventory(
        app_url="https://example.com",
        routes=routes,
        buttons=[],
        links=[],
        forms=[],
        headings=[],
        auth_signals=["login", "dashboard"],
        business_signals=["billing", "pricing", "checkout"],
    )


@pytest.mark.asyncio
async def test_capture_context_surfaces_crawl_diagnostics(tmp_db):
    from blop.tools.v2_surface import capture_context

    inventory = _inventory(["/pricing", "/billing"])
    inventory.crawl_metadata = {
        "mode": "parallel_section_aware",
        "worker_count": 2,
        "seeded_area_keys": ["/"],
        "area_page_counts": {"billing": 1, "pricing": 1},
        "timing_ms": 42,
        "error_count": 0,
    }

    with pytest.MonkeyPatch.context() as monkeypatch:
        monkeypatch.setattr("blop.engine.discovery.inventory_site", AsyncMock(return_value=inventory))
        monkeypatch.setattr("blop.storage.sqlite.save_site_inventory", AsyncMock())
        monkeypatch.setattr("blop.storage.sqlite.get_latest_context_graph", AsyncMock(return_value=None))
        monkeypatch.setattr("blop.storage.sqlite.list_flows", AsyncMock(return_value=[]))
        monkeypatch.setattr("blop.storage.sqlite.get_flow", AsyncMock(return_value=None))
        monkeypatch.setattr("blop.storage.sqlite.save_context_graph", AsyncMock())

        result = await capture_context("https://example.com")

    assert result["crawl_diagnostics"]["mode"] == "parallel_section_aware"
    assert result["crawl_diagnostics"]["worker_count"] == 2


@pytest.mark.asyncio
async def test_compare_context_reports_release_scope_and_uncovered_journeys(tmp_db):
    from blop.engine.context_graph import build_context_graph
    from blop.storage.sqlite import save_context_graph
    from blop.tools.v2_surface import compare_context

    previous = build_context_graph(
        "https://example.com",
        _inventory(["/pricing", "/billing"]),
        [
            {
                "flow_name": "checkout_flow",
                "goal": "Complete checkout",
                "starting_url": "https://example.com/billing",
                "business_criticality": "revenue",
            }
        ],
        profile_name="auth-profile",
        recorded_flows=[
            RecordedFlow(
                flow_id="rf_checkout",
                flow_name="checkout_flow",
                app_url="https://example.com",
                goal="Complete checkout",
                steps=[FlowStep(step_id=0, action="navigate", value="https://example.com/billing")],
                created_at=datetime.now(timezone.utc).isoformat(),
                business_criticality="revenue",
                entry_url="https://example.com/billing",
            )
        ],
    )
    candidate = build_context_graph(
        "https://example.com",
        _inventory(["/pricing", "/billing", "/settings"]),
        [
            {
                "flow_name": "checkout_flow",
                "goal": "Complete checkout",
                "starting_url": "https://example.com/billing",
                "business_criticality": "revenue",
            },
            {
                "flow_name": "signup_flow",
                "goal": "Sign up",
                "starting_url": "https://example.com/pricing",
                "business_criticality": "activation",
            },
        ],
        profile_name="auth-profile",
    )

    await save_context_graph(previous)
    await save_context_graph(candidate)

    result = await compare_context("https://example.com", previous.graph_id, candidate.graph_id)

    assert result["release_scope"]["changed_journeys"]
    assert "checkout_flow" in result["release_scope"]["changed_journeys"]
    assert result["impact_summary"][0]["criticality"] == "revenue"


@pytest.mark.asyncio
async def test_suggest_flows_for_diff_prefers_recorded_critical_journey(tmp_db):
    from blop.engine.context_graph import build_context_graph
    from blop.storage.sqlite import save_context_graph, save_flow
    from blop.tools.v2_surface import suggest_flows_for_diff

    recorded = RecordedFlow(
        flow_id="rf_billing",
        flow_name="billing_upgrade",
        app_url="https://example.com",
        goal="Upgrade billing plan",
        steps=[FlowStep(step_id=0, action="navigate", value="https://example.com/settings/billing")],
        created_at=datetime.now(timezone.utc).isoformat(),
        business_criticality="revenue",
        entry_url="https://example.com/settings/billing",
    )
    await save_flow(recorded)
    graph = build_context_graph(
        "https://example.com",
        _inventory(["/settings/billing", "/settings/profile"]),
        [
            {
                "flow_name": "billing_upgrade",
                "goal": "Upgrade billing plan",
                "starting_url": "https://example.com/settings/billing",
                "business_criticality": "revenue",
            },
            {
                "flow_name": "profile_update",
                "goal": "Update profile",
                "starting_url": "https://example.com/settings/profile",
                "business_criticality": "retention",
            },
        ],
        profile_name="auth-profile",
        recorded_flows=[recorded],
    )
    await save_context_graph(graph)

    result = await suggest_flows_for_diff(
        app_url="https://example.com",
        changed_files=["src/features/billing/upgrade.tsx"],
        changed_routes=["/settings/billing"],
        limit=3,
    )

    assert result["suggestions"]
    top = result["suggestions"][0]
    assert top["intent_label"] == "billing_upgrade"
    assert top["coverage_status"] == "recorded"
    assert top["flow_id"] == "rf_billing"


@pytest.mark.asyncio
async def test_assess_release_risk_uses_release_scope_context(tmp_db):
    from blop.engine.context_graph import build_context_graph
    from blop.storage import sqlite
    from blop.tools.v2_surface import assess_release_risk

    previous = build_context_graph(
        "https://example.com",
        _inventory(["/pricing", "/billing"]),
        [
            {
                "flow_name": "checkout_flow",
                "goal": "Complete checkout",
                "starting_url": "https://example.com/billing",
                "business_criticality": "revenue",
            }
        ],
        profile_name="auth-profile",
        recorded_flows=[
            RecordedFlow(
                flow_id="rf_checkout",
                flow_name="checkout_flow",
                app_url="https://example.com",
                goal="Complete checkout",
                steps=[FlowStep(step_id=0, action="navigate", value="https://example.com/billing")],
                created_at=datetime.now(timezone.utc).isoformat(),
                business_criticality="revenue",
                entry_url="https://example.com/billing",
            )
        ],
    )
    candidate = build_context_graph(
        "https://example.com",
        _inventory(["/pricing", "/billing", "/signup"]),
        [
            {
                "flow_name": "checkout_flow",
                "goal": "Complete checkout",
                "starting_url": "https://example.com/billing",
                "business_criticality": "revenue",
            },
            {
                "flow_name": "signup_flow",
                "goal": "Sign up",
                "starting_url": "https://example.com/signup",
                "business_criticality": "activation",
            },
        ],
        profile_name="auth-profile",
    )
    await sqlite.save_context_graph(previous)
    await sqlite.save_context_graph(candidate)

    run_id = "run_candidate"
    await sqlite.create_run(
        run_id=run_id,
        app_url="https://example.com",
        profile_name="auth-profile",
        flow_ids=["flow_signup"],
        headless=True,
        artifacts_dir="/tmp/artifacts",
        run_mode="hybrid",
    )
    await sqlite.save_case(
        FailureCase(
            run_id=run_id,
            flow_id="flow_signup",
            flow_name="signup_flow",
            status="fail",
            severity="blocker",
            business_criticality="activation",
            assertion_failures=["Expected signup completion"],
        )
    )

    result = await assess_release_risk(
        app_url="https://example.com",
        baseline_ref={"graph_id": previous.graph_id},
        candidate_ref={"graph_id": candidate.graph_id, "run_id": run_id},
    )

    assert result["risk_score"] > 0
    assert any("newly_uncovered" in " ".join(risk["evidence"]) for risk in result["top_risks"])
