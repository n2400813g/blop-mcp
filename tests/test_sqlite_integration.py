"""Integration tests against a real file-based (temporary) SQLite database.

These tests exercise the actual SQL queries and migrations — no mocking.
"""

from __future__ import annotations

import os
import tempfile
import uuid
from datetime import datetime, timezone

import pytest

# Use a temporary DB file for each test so we don't collide with real data.
_TMP_DIR = tempfile.mkdtemp()
os.environ["BLOP_DB_PATH"] = os.path.join(_TMP_DIR, "test_runs.db")

from blop.schemas import (
    AuthProfile,
    FailureCase,
    FlowStep,
    IncidentCluster,
    RecordedFlow,
    SiteContextGraph,
    TelemetrySignal,
)
from blop.storage import sqlite


@pytest.fixture(autouse=True)
async def _init():
    await sqlite.init_db()


# ---------------------------------------------------------------------------
# Auth profiles
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_save_and_get_auth_profile():
    profile = AuthProfile(
        profile_name="test-profile",
        auth_type="env_login",
        login_url="https://example.com/login",
    )
    await sqlite.save_auth_profile(profile, "/tmp/state.json")

    loaded = await sqlite.get_auth_profile("test-profile")
    assert loaded is not None
    assert loaded.profile_name == "test-profile"
    assert loaded.auth_type == "env_login"


@pytest.mark.asyncio
async def test_list_auth_profiles():
    p1 = AuthProfile(profile_name="profile-a", auth_type="env_login", login_url="https://example.com/login")
    p2 = AuthProfile(profile_name="profile-b", auth_type="storage_state", storage_state_path="/tmp/state.json")
    await sqlite.save_auth_profile(p1)
    await sqlite.save_auth_profile(p2)

    profiles = await sqlite.list_auth_profiles()
    names = {p["profile_name"] for p in profiles}
    assert "profile-a" in names
    assert "profile-b" in names


@pytest.mark.asyncio
async def test_get_missing_auth_profile():
    result = await sqlite.get_auth_profile("nonexistent")
    assert result is None


# ---------------------------------------------------------------------------
# Recorded flows
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_save_and_get_flow():
    flow = RecordedFlow(
        flow_name="login-flow",
        app_url="https://example.com",
        goal="Log in and see dashboard",
        steps=[
            FlowStep(step_id=0, action="navigate", selector=None, value="https://example.com/login"),
            FlowStep(step_id=1, action="fill", selector="#email", value="user@test.com"),
            FlowStep(step_id=2, action="click", selector="#submit", value=None),
        ],
        created_at=datetime.now(timezone.utc).isoformat(),
        business_criticality="activation",
    )
    await sqlite.save_flow(flow)

    loaded = await sqlite.get_flow(flow.flow_id)
    assert loaded is not None
    assert loaded.flow_name == "login-flow"
    assert len(loaded.steps) == 3
    assert loaded.business_criticality == "activation"


@pytest.mark.asyncio
async def test_list_flows():
    flow = RecordedFlow(
        flow_name="checkout-flow",
        app_url="https://example.com",
        goal="Complete checkout",
        steps=[FlowStep(step_id=0, action="navigate", selector=None, value="https://example.com")],
        created_at=datetime.now(timezone.utc).isoformat(),
    )
    await sqlite.save_flow(flow)

    flows = await sqlite.list_flows()
    names = [f["flow_name"] for f in flows]
    assert "checkout-flow" in names


# ---------------------------------------------------------------------------
# Runs
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_run_lifecycle():
    run_id = uuid.uuid4().hex
    flow_id = uuid.uuid4().hex

    await sqlite.create_run(
        run_id=run_id,
        app_url="https://example.com",
        profile_name=None,
        flow_ids=[flow_id],
        headless=True,
        artifacts_dir="/tmp/artifacts",
        run_mode="hybrid",
    )

    run = await sqlite.get_run(run_id)
    assert run is not None
    assert run["status"] == "queued"

    await sqlite.update_run_status(run_id, "running")
    run = await sqlite.get_run(run_id)
    assert run["status"] == "running"

    case = FailureCase(
        run_id=run_id,
        flow_id=flow_id,
        flow_name="test",
        status="pass",
        severity="none",
    )
    next_actions = ["Fix the login button", "Check API response"]
    await sqlite.update_run(run_id, "completed", [case], datetime.now(timezone.utc).isoformat(), next_actions)

    run = await sqlite.get_run(run_id)
    assert run["status"] == "completed"
    assert run["next_actions"] == next_actions


@pytest.mark.asyncio
async def test_create_run_with_initial_events_persists_transactional_startup():
    run_id = uuid.uuid4().hex
    queued_payload = {
        "app_url": "https://example.com",
        "flow_count": 2,
        "run_mode": "hybrid",
        "profile_name": "prod",
        "startup_timing_ms": {
            "flow_lookup": 10,
            "auth_resolve": 5,
            "auth_validate": 3,
            "db_persist": 0,
            "total_launch": 0,
        },
    }
    auth_payload = {
        "profile_name": "prod",
        "auth_used": True,
        "auth_source": "storage_state",
        "storage_state_path": "/tmp/auth.json",
        "user_data_dir": None,
        "session_validation_status": "valid",
        "startup_timing_ms": {
            "flow_lookup": 10,
            "auth_resolve": 5,
            "auth_validate": 3,
            "db_persist": 0,
            "total_launch": 0,
        },
    }

    await sqlite.create_run_with_initial_events(
        run_id=run_id,
        app_url="https://example.com",
        profile_name="prod",
        flow_ids=["flow-1", "flow-2"],
        headless=True,
        artifacts_dir="/tmp/artifacts",
        run_mode="hybrid",
        status="queued",
        run_queued_payload=queued_payload,
        auth_context_payload=auth_payload,
    )

    run = await sqlite.get_run(run_id)
    events = await sqlite.list_run_health_events(run_id)
    assert run is not None
    assert run["status"] == "queued"
    event_types = [event["event_type"] for event in events]
    assert "run_queued" in event_types
    assert "auth_context_resolved" in event_types


@pytest.mark.asyncio
async def test_list_runs():
    run_id = uuid.uuid4().hex
    await sqlite.create_run(run_id, "https://example.com", None, [], True, "/tmp/a", "hybrid")
    runs = await sqlite.list_runs(limit=5)
    ids = [r["run_id"] for r in runs]
    assert run_id in ids


# ---------------------------------------------------------------------------
# Run cases
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_save_and_list_cases():
    run_id = uuid.uuid4().hex
    flow_id = uuid.uuid4().hex
    await sqlite.create_run(run_id, "https://example.com", None, [flow_id], True, "/tmp/a", "hybrid")

    case = FailureCase(
        run_id=run_id,
        flow_id=flow_id,
        flow_name="my-flow",
        status="fail",
        severity="high",
        replay_mode="hybrid_repair",
        step_failure_index=2,
        business_criticality="revenue",
    )
    await sqlite.save_case(case)

    cases = await sqlite.list_cases_for_run(run_id)
    assert len(cases) >= 1
    assert cases[0].flow_name == "my-flow"
    assert cases[0].severity == "high"


# ---------------------------------------------------------------------------
# Site inventories
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_save_and_get_inventory():
    inventory_data = {
        "app_url": "https://example.com",
        "routes": ["/", "/pricing"],
        "buttons": [{"text": "Sign Up"}],
        "links": [],
        "forms": [],
        "headings": ["Welcome"],
        "auth_signals": ["sign in"],
        "business_signals": ["pricing"],
    }
    await sqlite.save_site_inventory("https://example.com", inventory_data)
    loaded = await sqlite.get_latest_site_inventory("https://example.com")
    assert loaded is not None
    assert loaded["app_url"] == "https://example.com"


# ---------------------------------------------------------------------------
# Context graphs
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_save_and_get_context_graph():
    from blop.schemas import ContextNode

    graph = SiteContextGraph(
        app_url="https://example.com",
        archetype="saas_app",
        created_at=datetime.now(timezone.utc).isoformat(),
        nodes=[ContextNode(node_id="route:/", node_type="route", label="/")],
        edges=[],
    )
    await sqlite.save_context_graph(graph)

    loaded = await sqlite.get_latest_context_graph("https://example.com")
    assert loaded is not None
    assert loaded.archetype == "saas_app"


# ---------------------------------------------------------------------------
# Run health events
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_run_health_events():
    run_id = uuid.uuid4().hex
    await sqlite.create_run(run_id, "https://example.com", None, [], True, "/tmp/a", "hybrid")

    await sqlite.save_run_health_event(run_id, "run_started", {"flow_count": 3})
    await sqlite.save_run_health_event(run_id, "case_completed", {"case_id": "c1"})

    events = await sqlite.list_run_health_events(run_id)
    assert len(events) == 2
    assert events[0]["event_type"] == "run_started"


# ---------------------------------------------------------------------------
# Telemetry signals
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_telemetry_signals():
    signals = [
        TelemetrySignal(
            app_url="https://example.com",
            source="sentry",
            ts=datetime.now(timezone.utc).isoformat(),
            signal_type="error_rate",
            value=0.05,
            unit="ratio",
        ),
    ]
    await sqlite.save_telemetry_signals(signals)
    loaded = await sqlite.list_telemetry_signals("https://example.com")
    assert len(loaded) >= 1


# ---------------------------------------------------------------------------
# Incident clusters
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_incident_clusters():
    cluster = IncidentCluster(
        app_url="https://example.com",
        title="Login fails after deploy",
        severity="blocker",
        affected_flows=1,
        first_seen=datetime.now(timezone.utc).isoformat(),
        last_seen=datetime.now(timezone.utc).isoformat(),
        affected_criticality=["activation"],
        member_case_ids=["case-1"],
    )
    await sqlite.save_incident_cluster(cluster)

    loaded = await sqlite.get_incident_cluster(cluster.cluster_id)
    assert loaded is not None
    assert loaded.title == "Login fails after deploy"

    open_clusters = await sqlite.list_open_incident_clusters("https://example.com")
    assert len(open_clusters) >= 1
