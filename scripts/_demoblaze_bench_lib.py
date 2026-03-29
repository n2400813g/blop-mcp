#!/usr/bin/env python3
"""Shared helpers for DemoBlaze MCP benchmark scripts."""

from __future__ import annotations

import asyncio
import json
import math
import os
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
DEMOBLAZE_URL = "https://www.demoblaze.com"
DEFAULT_ITERATIONS = 5

FLOW_TARGETS = [
    {
        "flow_name": "demoblaze_revenue_checkout",
        "goal": (
            "Navigate to https://www.demoblaze.com, open a product card, add the product to cart, "
            "open the cart page, place an order with synthetic purchaser details, and verify the order "
            "confirmation dialog appears with a confirmation message."
        ),
        "business_criticality": "revenue",
    },
    {
        "flow_name": "demoblaze_activation_catalog_navigation",
        "goal": (
            "Navigate to https://www.demoblaze.com, browse the catalog, switch between category filters, "
            "open at least one product details page, and verify the merchandising content and add-to-cart "
            "call to action remain visible."
        ),
        "business_criticality": "activation",
    },
    {
        "flow_name": "demoblaze_cart_management",
        "goal": (
            "Navigate to https://www.demoblaze.com, add a product to cart, open the cart page, remove the item, "
            "return to the catalog, add a product again, and verify the cart page shows the item after re-adding it."
        ),
        "business_criticality": "support",
    },
]


def ensure_src_on_path() -> None:
    src = str(SRC)
    if src not in sys.path:
        sys.path.insert(0, src)


def repo_pythonpath(env: dict[str, str] | None = None) -> dict[str, str]:
    merged = dict(env or os.environ)
    current = merged.get("PYTHONPATH", "")
    merged["PYTHONPATH"] = f"{SRC}{os.pathsep}{current}" if current else str(SRC)
    merged.setdefault("UV_CACHE_DIR", str(Path(tempfile.gettempdir()) / "uv-cache"))
    return merged


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def percentile(values: list[float], pct: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    if len(ordered) == 1:
        return ordered[0]
    rank = (len(ordered) - 1) * pct
    lower = math.floor(rank)
    upper = math.ceil(rank)
    if lower == upper:
        return ordered[lower]
    weight = rank - lower
    return ordered[lower] * (1.0 - weight) + ordered[upper] * weight


def summarize_samples(samples: list[dict[str, Any]]) -> dict[str, Any]:
    durations = [float(sample["seconds"]) for sample in samples if sample.get("seconds") is not None]
    successes = [bool(sample.get("ok", False)) for sample in samples]
    p50 = percentile(durations, 0.50)
    p95 = percentile(durations, 0.95)
    mean = sum(durations) / len(durations) if durations else None
    variance = (
        sum((duration - mean) ** 2 for duration in durations) / len(durations)
        if durations and mean is not None
        else None
    )
    return {
        "n": len(samples),
        "success_rate": (sum(1 for ok in successes if ok) / len(successes)) if successes else None,
        "p50_s": p50,
        "p95_s": p95,
        "min_s": min(durations) if durations else None,
        "max_s": max(durations) if durations else None,
        "mean_s": mean,
        "variance_s2": variance,
        "p95_over_p50": (p95 / p50) if p50 and p95 else None,
        "samples": samples,
    }


def metric(seconds: float | None, ok: bool, **extra: Any) -> dict[str, Any]:
    payload: dict[str, Any] = {"seconds": seconds, "ok": ok}
    payload.update(extra)
    return payload


def aggregate_phase_maps(iterations: list[dict[str, Any]]) -> dict[str, Any]:
    grouped: dict[str, dict[str, list[dict[str, Any]]]] = {}
    for iteration in iterations:
        for phase_name, metrics in (iteration.get("phases") or {}).items():
            phase_group = grouped.setdefault(phase_name, {})
            for metric_name, metric_value in (metrics or {}).items():
                if isinstance(metric_value, dict) and "seconds" in metric_value and "ok" in metric_value:
                    phase_group.setdefault(metric_name, []).append(metric_value)
    return {
        phase_name: {metric_name: summarize_samples(samples) for metric_name, samples in metrics.items()}
        for phase_name, metrics in grouped.items()
    }


def format_seconds(value: float | None) -> str | None:
    return None if value is None else round(value, 4)


def compact_summary(summary: dict[str, Any]) -> dict[str, Any]:
    return {
        key: format_seconds(value) if key.endswith("_s") else value
        for key, value in summary.items()
        if key != "samples"
    }


def build_iteration_table(iterations: list[dict[str, Any]]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for iteration in iterations:
        workflow = iteration.get("workflow") or {}
        replay = (iteration.get("phases", {}).get("canonical_workflow", {}) or {}).get("replay_to_terminal")
        rows.append(
            {
                "iteration": iteration.get("iteration"),
                "temperature": iteration.get("temperature"),
                "workflow_ok": workflow.get("ok"),
                "decision": workflow.get("decision"),
                "run_status": workflow.get("status"),
                "flow_count": workflow.get("recorded_flow_count"),
                "workflow_total_s": format_seconds(workflow.get("total_seconds")),
                "replay_to_terminal_s": format_seconds((replay or {}).get("seconds")),
                "top_failure_mode": workflow.get("top_failure_mode"),
                "stability_bucket": workflow.get("stability_bucket"),
            }
        )
    return rows


def judge_latency(seconds: float | None, *, good: float, review: float) -> str:
    if seconds is None:
        return "not_measured"
    if seconds < good:
        return "good"
    if seconds <= review:
        return "review"
    return "poor"


def judge_variability(ratio: float | None, *, max_ratio: float) -> str:
    if ratio is None:
        return "not_measured"
    return "stable" if ratio <= max_ratio else "unstable"


def build_verdicts(aggregated: dict[str, Any], iterations: list[dict[str, Any]]) -> dict[str, Any]:
    stdio = ((aggregated.get("mcp_stdio") or {}).get("initialize")) or {}
    resource = ((aggregated.get("resource_hotpaths") or {}).get("journeys_resource")) or {}
    workflow = ((aggregated.get("canonical_workflow") or {}).get("replay_to_terminal")) or {}
    validate = ((aggregated.get("canonical_workflow") or {}).get("validate_release_setup")) or {}
    discovery = ((aggregated.get("canonical_workflow") or {}).get("discover_critical_journeys")) or {}
    triage = ((aggregated.get("canonical_workflow") or {}).get("triage_release_blocker")) or {}

    browser = ((aggregated.get("atomic_browser") or {}).get("navigate_to_url")) or {}
    performance_lcp: list[float] = []
    performance_fcp: list[float] = []
    workflow_successes = 0
    workflow_total = 0
    blockers: list[str] = []

    for iteration in iterations:
        wf = iteration.get("workflow") or {}
        if wf:
            workflow_total += 1
            if wf.get("ok"):
                workflow_successes += 1
            if wf.get("environment_blocker"):
                blockers.append(wf["environment_blocker"])
        for metric in wf.get("navigation_metrics", []) or []:
            if metric.get("largestContentfulPaint") is not None:
                performance_lcp.append(float(metric["largestContentfulPaint"]) / 1000.0)
            if metric.get("firstContentfulPaint") is not None:
                performance_fcp.append(float(metric["firstContentfulPaint"]) / 1000.0)

    workflow_success_rate = (workflow_successes / workflow_total) if workflow_total else None

    transport_ready = (
        (stdio.get("success_rate") == 1.0 if stdio else False)
        and (resource.get("success_rate") == 1.0 if resource else False)
        and judge_variability(stdio.get("p95_over_p50"), max_ratio=2.0) != "unstable"
        and judge_variability(resource.get("p95_over_p50"), max_ratio=2.0) != "unstable"
    )
    workflow_ready = (
        not blockers
        and workflow_success_rate is not None
        and workflow_success_rate >= 0.8
        and judge_variability(workflow.get("p95_over_p50"), max_ratio=2.5) != "unstable"
    )

    p95_lcp = percentile(performance_lcp, 0.95)
    p95_fcp = percentile(performance_fcp, 0.95)
    journey_ready = (
        p95_lcp is not None
        and p95_fcp is not None
        and judge_latency(p95_lcp, good=2.5, review=4.0) != "poor"
        and judge_latency(p95_fcp, good=1.8, review=3.0) != "poor"
    )

    final_verdict = "enterprise-ready"
    if blockers or not transport_ready or workflow_success_rate == 0:
        final_verdict = "not enterprise-ready"
    elif not workflow_ready or not journey_ready:
        final_verdict = "usable with caveats"

    return {
        "tooling_transport_verdict": {
            "status": "pass" if transport_ready else "review",
            "initialize_latency": judge_latency(stdio.get("p95_s"), good=2.0, review=5.0),
            "metadata_hotpath_latency": judge_latency(resource.get("p95_s"), good=0.35, review=1.0),
            "variability": judge_variability(stdio.get("p95_over_p50"), max_ratio=2.0),
        },
        "workflow_reliability_verdict": {
            "status": "pass" if workflow_ready else "review",
            "workflow_success_rate": workflow_success_rate,
            "validate_latency": judge_latency(validate.get("p95_s"), good=10.0, review=20.0),
            "discovery_latency": judge_latency(discovery.get("p95_s"), good=30.0, review=60.0),
            "replay_latency": judge_latency(workflow.get("p95_s"), good=120.0, review=300.0),
            "triage_latency": judge_latency(triage.get("p95_s"), good=15.0, review=30.0),
            "variability": judge_variability(workflow.get("p95_over_p50"), max_ratio=2.5),
            "environment_blockers": blockers,
        },
        "user_journey_performance_verdict": {
            "status": "pass" if journey_ready else "review",
            "navigate_latency": judge_latency(browser.get("p95_s"), good=3.0, review=8.0),
            "p95_lcp_s": format_seconds(p95_lcp),
            "p95_fcp_s": format_seconds(p95_fcp),
            "lcp_rating": judge_latency(p95_lcp, good=2.5, review=4.0),
            "fcp_rating": judge_latency(p95_fcp, good=1.8, review=3.0),
        },
        "final_verdict": final_verdict,
    }


def print_json(payload: dict[str, Any]) -> None:
    json.dump(payload, sys.stdout, indent=2, sort_keys=True)
    sys.stdout.write("\n")


@dataclass
class SubprocessJsonResult:
    data: dict[str, Any]
    returncode: int
    stdout: str
    stderr: str


def run_json_subprocess(cmd: list[str], *, env: dict[str, str], cwd: Path, timeout: int) -> SubprocessJsonResult:
    proc = subprocess.run(
        cmd,
        cwd=str(cwd),
        capture_output=True,
        text=True,
        timeout=timeout,
        env=env,
    )
    stdout = proc.stdout.strip()
    data = json.loads(stdout) if stdout else {}
    return SubprocessJsonResult(data=data, returncode=proc.returncode, stdout=proc.stdout, stderr=proc.stderr)


async def timed_call(awaitable_factory, *args: Any, **kwargs: Any) -> tuple[float, Any]:
    started = time.perf_counter()
    result = await awaitable_factory(*args, **kwargs)
    elapsed = time.perf_counter() - started
    return elapsed, result


def make_temp_paths(iteration: int) -> dict[str, str]:
    root = Path(tempfile.mkdtemp(prefix=f"demoblaze-bench-{iteration:02d}-"))
    return {
        "root": str(root),
        "db_path": str(root / "bench.db"),
        "runs_dir": str(root / "runs"),
        "iteration_json": str(root / f"iteration_{iteration:02d}.json"),
    }


async def seed_flows(app_url: str, count: int) -> list[str]:
    ensure_src_on_path()
    from blop.schemas import FlowStep, RecordedFlow
    from blop.storage import sqlite

    flow_ids: list[str] = []
    for idx in range(count):
        flow = RecordedFlow(
            flow_id=f"bench-flow-{idx:03d}",
            flow_name=f"bench_flow_{idx:03d}",
            app_url=app_url,
            goal=f"Load benchmark route {idx}",
            steps=[
                FlowStep(
                    step_id=0,
                    action="navigate",
                    value=f"{app_url}/route-{idx % 7}",
                    description="Navigate to benchmark route",
                )
            ],
            created_at=utc_now(),
            business_criticality="revenue" if idx % 2 == 0 else "activation",
        )
        await sqlite.save_flow(flow)
        flow_ids.append(flow.flow_id)
    return flow_ids


async def poll_run(get_test_results, run_id: str, *, timeout_secs: int, poll_secs: float = 2.0) -> dict[str, Any]:
    deadline = time.monotonic() + timeout_secs
    last: dict[str, Any] | None = None
    while time.monotonic() < deadline:
        last = await get_test_results(run_id=run_id)
        if last.get("status") not in {"queued", "running"}:
            return last
        await asyncio.sleep(poll_secs)
    return last or {"error": f"Timed out waiting for run {run_id}"}
