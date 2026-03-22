"""MCP resource shape tests — real SQLite, no browser mocks."""
from __future__ import annotations

import os
from datetime import datetime, timezone

import pytest

from blop.schemas import FlowStep, IncidentCluster, RecordedFlow


def _make_flow(flow_id: str = "f1", criticality: str = "revenue") -> RecordedFlow:
    return RecordedFlow(
        flow_id=flow_id,
        flow_name="checkout",
        app_url="https://example.com",
        goal="Complete checkout",
        steps=[FlowStep(step_id=0, action="navigate", value="https://example.com")],
        created_at=datetime.now(timezone.utc).isoformat(),
        business_criticality=criticality,
    )


# ---------------------------------------------------------------------------
# blop://journeys
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_journeys_resource_empty_db(tmp_db):
    """Empty DB → journeys resource returns {journeys: [], total: 0}."""
    from blop.tools.resources import journeys_resource

    result = await journeys_resource()
    assert result["journeys"] == []
    assert result["total"] == 0


@pytest.mark.asyncio
async def test_journeys_resource_revenue_flow_is_gated(tmp_db):
    """Revenue flow saved in DB → include_in_release_gating=True in resource."""
    from blop.engine.context_graph import build_context_graph
    from blop.schemas import SiteInventory
    from blop.storage.sqlite import save_flow
    from blop.storage.sqlite import save_context_graph
    from blop.tools.resources import journeys_resource

    flow = _make_flow("rev-flow-1", "revenue")
    await save_flow(flow)
    graph = build_context_graph(
        "https://example.com",
        SiteInventory(
            app_url="https://example.com",
            routes=["/checkout"],
            buttons=[],
            links=[],
            forms=[],
            headings=[],
            auth_signals=["login"],
            business_signals=["checkout"],
        ),
        [],
        recorded_flows=[flow],
    )
    await save_context_graph(graph)

    result = await journeys_resource()
    assert result["total"] == 1
    journey = result["journeys"][0]
    assert journey["include_in_release_gating"] is True
    assert journey["journey_id"] == "rev-flow-1"
    assert journey["coverage_status"] == "recorded"


@pytest.mark.asyncio
async def test_journeys_resource_other_criticality_not_gated(tmp_db):
    """Support flow → include_in_release_gating=False."""
    from blop.storage.sqlite import save_flow
    from blop.tools.resources import journeys_resource

    flow = _make_flow("support-flow-1", "support")
    await save_flow(flow)

    result = await journeys_resource()
    journey = result["journeys"][0]
    assert journey["include_in_release_gating"] is False


# ---------------------------------------------------------------------------
# blop://release/{id}/brief
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_release_brief_resource_unknown_id_returns_error(tmp_db):
    """Unknown release_id → error dict (not exception)."""
    from blop.tools.resources import release_brief_resource

    result = await release_brief_resource("nonexistent-release-id")
    assert "error" in result


@pytest.mark.asyncio
async def test_release_brief_resource_after_save_returns_correct_fields(tmp_db):
    """After saving a brief, the resource returns the expected fields."""
    from blop.engine.context_graph import build_context_graph
    from blop.schemas import SiteInventory
    from blop.storage.sqlite import save_context_graph
    from blop.storage.sqlite import save_release_brief
    from blop.tools.resources import release_brief_resource

    brief = {
        "release_id": "rel-100",
        "run_id": "run-abc",
        "app_url": "https://example.com",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "decision": "SHIP",
        "risk": {"value": 5, "level": "low"},
        "confidence": {"value": 0.9, "label": "high"},
        "blocker_count": 0,
        "blocker_journey_names": [],
        "critical_journey_failures": 0,
        "top_actions": [],
    }
    await save_release_brief("rel-100", "run-abc", "https://example.com", brief)
    graph = build_context_graph(
        "https://example.com",
        SiteInventory(
            app_url="https://example.com",
            routes=["/checkout"],
            buttons=[],
            links=[],
            forms=[],
            headings=[],
            auth_signals=["login"],
            business_signals=["checkout"],
        ),
        [{"flow_name": "checkout", "goal": "Complete checkout", "business_criticality": "revenue"}],
    )
    await save_context_graph(graph)

    result = await release_brief_resource("rel-100")
    assert result.get("decision") == "SHIP"
    assert result.get("run_id") == "run-abc"
    assert "error" not in result
    assert result["context_graph_summary"]["critical_journey_count"] >= 1


# ---------------------------------------------------------------------------
# blop://release/{id}/artifacts
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_release_artifacts_resource_no_run_returns_error(tmp_db):
    """No run linked to release → artifacts resource returns error."""
    from blop.tools.resources import release_artifacts_resource

    result = await release_artifacts_resource("no-such-release")
    assert "error" in result
    assert "artifacts" in result


# ---------------------------------------------------------------------------
# blop://release/{id}/incidents
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_release_incidents_resource_empty_returns_empty_list(tmp_db):
    """No incidents for an unknown release → incidents: [], total: 0 (or error)."""
    from blop.tools.resources import release_incidents_resource

    result = await release_incidents_resource("no-such-release")
    # Either an error dict or empty incidents list
    assert "error" in result or result.get("total", 0) == 0


@pytest.mark.asyncio
async def test_release_incidents_resource_includes_journey_context(tmp_db):
    from blop.storage.sqlite import save_incident_cluster, save_release_brief
    from blop.tools.resources import release_incidents_resource

    await save_release_brief(
        "rel-200",
        "run-200",
        "https://example.com",
        {
            "release_id": "rel-200",
            "run_id": "run-200",
            "app_url": "https://example.com",
        },
    )
    cluster = IncidentCluster(
        cluster_id="cluster-200",
        app_url="https://example.com",
        title="Repeated failure at billing_upgrade#step_1",
        severity="high",
        affected_flows=1,
        affected_criticality=["revenue"],
        first_seen="run-200",
        last_seen="run-200",
        evidence_refs=["run:run-200/case:case-1"],
        member_case_ids=["case-1"],
        metadata={
            "linked_journey": "billing_upgrade",
            "entry_routes": ["/settings/billing"],
            "areas": ["billing"],
            "coverage_status": "recorded",
            "next_checks": ["Verify auth/session preconditions before rerunning billing_upgrade."],
        },
    )
    await save_incident_cluster(cluster)

    result = await release_incidents_resource("rel-200")
    assert result["total"] == 1
    assert result["incidents"][0]["journey_context"]["linked_journey"] == "billing_upgrade"
