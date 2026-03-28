from __future__ import annotations

import asyncio
import time
from datetime import datetime, timezone
from unittest.mock import patch

import pytest

from blop.schemas import FlowStep, RecordedFlow

BENCH_APP_URL = "https://bench.example.com"


def _done_future():
    loop = asyncio.get_running_loop()
    fut = loop.create_future()
    fut.set_result(None)
    return fut


def _discard_background_task(coro):
    if asyncio.iscoroutine(coro):
        coro.close()
    return _done_future()


async def _seed_flows(count: int) -> list[RecordedFlow]:
    from blop.storage import sqlite

    flows: list[RecordedFlow] = []
    for idx in range(count):
        flow = RecordedFlow(
            flow_id=f"perf-flow-{idx:03d}",
            flow_name=f"perf_flow_{idx:03d}",
            app_url=BENCH_APP_URL,
            goal=f"Load benchmark route {idx}",
            steps=[
                FlowStep(
                    step_id=0,
                    action="navigate",
                    value=f"{BENCH_APP_URL}/route-{idx % 7}",
                    description="Navigate to benchmark route",
                )
            ],
            created_at=datetime.now(timezone.utc).isoformat(),
            business_criticality="revenue" if idx % 2 == 0 else "activation",
        )
        await sqlite.save_flow(flow)
        flows.append(flow)
    return flows


@pytest.mark.asyncio
async def test_resource_hotpaths_smoke(tmp_db):
    from blop.tools import context_read
    from blop.tools.resources import journeys_resource

    await _seed_flows(120)

    t0 = time.perf_counter()
    journeys = await journeys_resource(app_url=BENCH_APP_URL)
    journeys_elapsed = time.perf_counter() - t0

    t0 = time.perf_counter()
    release_journeys = await context_read.get_journeys_for_release(app_url=BENCH_APP_URL)
    release_elapsed = time.perf_counter() - t0

    assert journeys["total"] == 120
    assert release_journeys["ok"] is True
    assert len(release_journeys["data"]["journeys"]) == 120
    assert journeys_elapsed < 0.35
    assert release_elapsed < 0.35


@pytest.mark.asyncio
async def test_run_regression_startup_smoke(tmp_db):
    from blop.tools.regression import run_regression_test

    flows = await _seed_flows(12)

    with patch("blop.config.check_llm_api_key", return_value=(True, "GOOGLE_API_KEY")):
        with patch("blop.tools.regression._spawn_background_task", side_effect=_discard_background_task):
            t0 = time.perf_counter()
            result = await run_regression_test(
                app_url=BENCH_APP_URL,
                flow_ids=[flow.flow_id for flow in flows],
                headless=True,
                run_mode="hybrid",
            )
            elapsed = time.perf_counter() - t0

    assert result["status"] == "queued"
    assert result["flow_count"] == 12
    assert elapsed < 1.0
