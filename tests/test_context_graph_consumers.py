from __future__ import annotations

from datetime import datetime, timezone

import pytest

from blop.schemas import FailureCase, FlowStep, RecordedFlow, SiteInventory, TelemetrySignal


def _inventory() -> SiteInventory:
    return SiteInventory(
        app_url="https://example.com",
        routes=["/pricing", "/settings/billing", "/dashboard"],
        buttons=[],
        links=[],
        forms=[],
        headings=[],
        auth_signals=["login", "dashboard"],
        business_signals=["pricing", "billing", "checkout"],
    )


def _recorded_billing_flow() -> RecordedFlow:
    return RecordedFlow(
        flow_id="flow_billing",
        flow_name="billing_upgrade",
        app_url="https://example.com",
        goal="Upgrade billing plan",
        steps=[FlowStep(step_id=0, action="navigate", value="https://example.com/settings/billing")],
        created_at=datetime.now(timezone.utc).isoformat(),
        business_criticality="revenue",
        entry_url="https://example.com/settings/billing",
    )


@pytest.mark.asyncio
async def test_cluster_incidents_enriches_with_journey_neighborhood(tmp_db):
    from blop.engine.context_graph import build_context_graph
    from blop.storage import sqlite
    from blop.tools.v2_surface import cluster_incidents

    recorded = _recorded_billing_flow()
    await sqlite.save_flow(recorded)
    graph = build_context_graph(
        "https://example.com",
        _inventory(),
        [
            {
                "flow_name": "billing_upgrade",
                "goal": "Upgrade billing plan",
                "starting_url": "https://example.com/settings/billing",
                "business_criticality": "revenue",
            }
        ],
        profile_name="auth-profile",
        recorded_flows=[recorded],
    )
    await sqlite.save_context_graph(graph)

    await sqlite.create_run(
        run_id="run_cluster",
        app_url="https://example.com",
        profile_name="auth-profile",
        flow_ids=[recorded.flow_id],
        headless=True,
        artifacts_dir="/tmp/artifacts",
        run_mode="hybrid",
    )
    for idx in range(2):
        await sqlite.save_case(
            FailureCase(
                case_id=f"case_{idx}",
                run_id="run_cluster",
                flow_id=recorded.flow_id,
                flow_name=recorded.flow_name,
                status="fail",
                severity="high",
                business_criticality="revenue",
                step_failure_index=1,
            )
        )

    result = await cluster_incidents("https://example.com", run_ids=["run_cluster"], min_cluster_size=2)

    assert result["cluster_count"] == 1
    cluster = result["clusters"][0]
    assert cluster["metadata"]["linked_journey"] == "billing_upgrade"
    assert "/settings/billing" in cluster["metadata"]["entry_routes"]
    assert cluster["metadata"]["areas"] == ["billing"]


@pytest.mark.asyncio
async def test_triage_and_results_include_context_graph_guidance(tmp_db):
    from blop.engine.context_graph import build_context_graph
    from blop.storage import sqlite
    from blop.tools.results import get_test_results
    from blop.tools.triage import triage_release_blocker

    recorded = _recorded_billing_flow()
    await sqlite.save_flow(recorded)
    graph = build_context_graph(
        "https://example.com",
        _inventory(),
        [
            {
                "flow_name": "billing_upgrade",
                "goal": "Upgrade billing plan",
                "starting_url": "https://example.com/settings/billing",
                "business_criticality": "revenue",
            }
        ],
        profile_name="auth-profile",
        recorded_flows=[recorded],
    )
    await sqlite.save_context_graph(graph)
    await sqlite.create_run(
        run_id="run_results",
        app_url="https://example.com",
        profile_name="auth-profile",
        flow_ids=[recorded.flow_id],
        headless=True,
        artifacts_dir="/tmp/artifacts",
        run_mode="hybrid",
    )
    case = FailureCase(
        case_id="case_results",
        run_id="run_results",
        flow_id=recorded.flow_id,
        flow_name=recorded.flow_name,
        status="fail",
        severity="blocker",
        business_criticality="revenue",
        assertion_failures=["Expected upgrade confirmation"],
    )
    await sqlite.save_case(case)
    await sqlite.update_run(
        "run_results",
        "completed",
        [case],
        completed_at=datetime.now(timezone.utc).isoformat(),
    )

    triage = await triage_release_blocker(run_id="run_results")
    report = await get_test_results("run_results")

    assert triage["evidence_summary_compact"]["failure_neighborhood"]["journey"] == "billing_upgrade"
    assert triage["next_checks"]
    assert report["context_graph_summary"]["critical_journey_count"] >= 1
    assert report["context_next_checks"]


@pytest.mark.asyncio
async def test_correlation_report_uses_linked_journey_and_route_context(tmp_db):
    from blop.engine.context_graph import build_context_graph
    from blop.storage import sqlite
    from blop.tools.v2_surface import cluster_incidents, get_correlation_report

    recorded = _recorded_billing_flow()
    await sqlite.save_flow(recorded)
    graph = build_context_graph(
        "https://example.com",
        _inventory(),
        [
            {
                "flow_name": "billing_upgrade",
                "goal": "Upgrade billing plan",
                "starting_url": "https://example.com/settings/billing",
                "business_criticality": "revenue",
            }
        ],
        profile_name="auth-profile",
        recorded_flows=[recorded],
    )
    await sqlite.save_context_graph(graph)
    await sqlite.create_run(
        run_id="run_corr",
        app_url="https://example.com",
        profile_name="auth-profile",
        flow_ids=[recorded.flow_id],
        headless=True,
        artifacts_dir="/tmp/artifacts",
        run_mode="hybrid",
    )
    for idx in range(2):
        await sqlite.save_case(
            FailureCase(
                case_id=f"case_corr_{idx}",
                run_id="run_corr",
                flow_id=recorded.flow_id,
                flow_name=recorded.flow_name,
                status="fail",
                severity="high",
                business_criticality="revenue",
                step_failure_index=1,
            )
        )
    await cluster_incidents("https://example.com", run_ids=["run_corr"], min_cluster_size=2)
    await sqlite.save_telemetry_signals(
        [
            TelemetrySignal(
                app_url="https://example.com",
                source="custom",
                ts=datetime.now(timezone.utc).isoformat(),
                signal_type="error_rate",
                journey_key="billing_upgrade",
                route="/settings/billing",
                value=2.0,
                unit="count",
            )
        ]
    )

    report = await get_correlation_report("https://example.com", min_confidence=0.6)

    assert report["matches"]
    assert report["matches"][0]["linked_journey"] == "billing_upgrade"
    assert report["matches"][0]["journey_key"] == "billing_upgrade"
