#!/usr/bin/env python3
"""
In-process benchmark for MCP tool code paths against DemoBlaze.

Measures handler + SQLite + synthetic replay wall time — not JSON-RPC stdio latency.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import math
import statistics
import sys
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Awaitable, Callable
from unittest.mock import AsyncMock, patch

_REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO_ROOT / "src"))

DEMOBLAZE_URL = "https://www.demoblaze.com"
BENCH_APP_URL = "https://bench.example.com"
TARGETS = {
    "warm_context_reads_p95_ms": 25.0,
    "warm_resource_reads_p95_ms": 35.0,
    "release_startup_p95_ms": 1000.0,
    "multi_flow_replay_overhead_p95_ms": 750.0,
}


def _stats_ms(samples: list[float]) -> dict[str, float]:
    if not samples:
        return {
            "min_ms": float("nan"),
            "max_ms": float("nan"),
            "mean_ms": float("nan"),
            "p50_ms": float("nan"),
            "p95_ms": float("nan"),
            "n": 0,
        }
    s = sorted(samples)
    n = len(s)
    mean = sum(s) / n
    p95_idx = max(0, min(n - 1, math.ceil(0.95 * n) - 1))
    return {
        "min_ms": s[0] * 1000,
        "max_ms": s[-1] * 1000,
        "mean_ms": mean * 1000,
        "p50_ms": statistics.median(s) * 1000,
        "p95_ms": s[p95_idx] * 1000,
        "n": n,
    }


async def _measure_phase(
    iterations: int,
    warmup: int,
    func: Callable[[], Awaitable[Any]],
) -> tuple[dict[str, float], list[Any]]:
    results: list[Any] = []
    timings: list[float] = []
    for idx in range(max(0, warmup) + max(1, iterations)):
        t0 = time.perf_counter()
        result = await func()
        elapsed = time.perf_counter() - t0
        if idx >= warmup:
            timings.append(elapsed)
            results.append(result)
    return _stats_ms(timings), results


def _target_status(actual_ms: float, target_ms: float) -> str:
    if math.isnan(actual_ms):
        return "unknown"
    return "pass" if actual_ms <= target_ms else "investigate"


def _discard_background_task(coro):
    if asyncio.iscoroutine(coro):
        coro.close()
    loop = asyncio.get_running_loop()
    fut = loop.create_future()
    fut.set_result(None)
    return fut


@dataclass
class BenchReport:
    target_url: str = DEMOBLAZE_URL
    seeded_app_url: str = BENCH_APP_URL
    iterations: int = 15
    warmup: int = 2
    seeded_flow_count: int = 0
    phases: dict[str, Any] = field(default_factory=dict)
    targets: dict[str, float] = field(default_factory=lambda: dict(TARGETS))
    warnings: list[str] = field(default_factory=list)


@dataclass
class BenchmarkConfig:
    iterations: int = 15
    warmup: int = 2
    skip_browser: bool = False
    skip_context: bool = False
    validate: bool = False
    json_out: bool = False
    seed_flows: int = 60
    replay_flow_count: int = 8
    skip_release: bool = False


async def _seed_benchmark_flows(app_url: str, count: int) -> list[Any]:
    from blop.schemas import FlowStep, RecordedFlow
    from blop.storage import sqlite

    existing = await sqlite.list_flows_full(app_url=app_url)
    existing_by_id = {flow.flow_id: flow for flow in existing}
    for idx in range(len(existing), count):
        flow_id = f"bench-flow-{idx:03d}"
        if flow_id in existing_by_id:
            continue
        flow = RecordedFlow(
            flow_id=flow_id,
            flow_name=f"benchmark_flow_{idx:03d}",
            app_url=app_url,
            goal=f"Load synthetic benchmark route {idx}",
            steps=[
                FlowStep(
                    step_id=0,
                    action="navigate",
                    value=f"{app_url}/route-{idx % 7}",
                    description="Navigate to seeded route",
                    url_after=f"{app_url}/route-{idx % 7}",
                ),
                FlowStep(
                    step_id=1,
                    action="wait",
                    value="0.05",
                    description="Allow the page to settle",
                ),
            ],
            created_at=datetime.now(timezone.utc).isoformat(),
            business_criticality=("revenue" if idx % 3 == 0 else "activation" if idx % 3 == 1 else "support"),
        )
        await sqlite.save_flow(flow)
        existing_by_id[flow_id] = flow
    return list(existing_by_id.values())[:count]


class _FakeTracing:
    def __init__(self, delay_s: float) -> None:
        self._delay_s = delay_s

    async def start(self, **kwargs) -> None:
        await asyncio.sleep(self._delay_s)

    async def stop(self, path: str) -> None:
        target = Path(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("trace")
        await asyncio.sleep(self._delay_s)


class _FakeCapturePage:
    def __init__(self, screenshot_delay_s: float) -> None:
        self.url = BENCH_APP_URL
        self._screenshot_delay_s = screenshot_delay_s

    async def goto(self, url: str, **kwargs) -> None:
        self.url = url

    async def wait_for_timeout(self, ms: int) -> None:
        await asyncio.sleep(0)

    async def wait_for_function(self, *args, **kwargs) -> None:
        return None

    async def wait_for_selector(self, *args, **kwargs) -> None:
        return None

    async def evaluate(self, *args, **kwargs):
        return []

    async def title(self) -> str:
        return "Bench"

    async def screenshot(self, path: str | None = None, **kwargs):
        if path:
            target = Path(path)
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(b"bench")
        await asyncio.sleep(self._screenshot_delay_s)
        return b"bench"

    def on(self, *args, **kwargs) -> None:
        return None


class _FakeCaptureContext:
    def __init__(self, page: _FakeCapturePage, trace_delay_s: float) -> None:
        self.pages = [page]
        self.tracing = _FakeTracing(trace_delay_s)

    async def close(self) -> None:
        return None


class _FakeCaptureLease:
    def __init__(self, page: _FakeCapturePage, trace_delay_s: float) -> None:
        self.browser = object()
        self.page = page
        self.context = _FakeCaptureContext(page, trace_delay_s)

    async def close(self) -> None:
        await self.context.close()


async def _benchmark_capture_policy_comparison(
    report: BenchReport,
    cfg: BenchmarkConfig,
) -> None:
    from blop.engine.evidence_policy import EvidencePolicy
    from blop.engine.regression import execute_recorded_flow
    from blop.schemas import FlowStep, RecordedFlow

    flow = RecordedFlow(
        flow_id="bench-capture-flow",
        flow_name="bench_capture_flow",
        app_url=BENCH_APP_URL,
        goal="Load the benchmark route",
        steps=[
            FlowStep(step_id=0, action="navigate", value=BENCH_APP_URL, description="Navigate to benchmark page"),
            FlowStep(step_id=1, action="wait", value="0.01", description="Wait briefly"),
        ],
        created_at=datetime.now(timezone.utc).isoformat(),
        business_criticality="support",
    )
    minimal = EvidencePolicy(
        trace=False,
        video=False,
        screenshots_enabled=False,
        console=True,
        network=True,
        periodic_screenshots=False,
        navigation_screenshots=False,
        step_screenshots=False,
        failure_screenshots=False,
        final_screenshot=False,
        screenshot_interval_secs=30.0,
        max_screenshots=1,
        artifact_cap=1,
    )
    forensic = EvidencePolicy(
        trace=True,
        video=False,
        screenshots_enabled=True,
        console=True,
        network=True,
        periodic_screenshots=False,
        navigation_screenshots=True,
        step_screenshots=True,
        failure_screenshots=True,
        final_screenshot=True,
        screenshot_interval_secs=30.0,
        max_screenshots=10,
        artifact_cap=10,
    )

    async def _measure_policy(policy: EvidencePolicy) -> dict[str, float]:
        async def _run_once():
            lease = _FakeCaptureLease(_FakeCapturePage(screenshot_delay_s=0.004), trace_delay_s=0.003)
            with patch("blop.engine.regression.resolve_evidence_policy", return_value=policy):
                with patch("blop.engine.regression.BROWSER_POOL.acquire", new=AsyncMock(return_value=lease)):
                    with patch("blop.tools.network.apply_routes_to_context", new=AsyncMock(return_value=None)):
                        return await execute_recorded_flow(
                            flow=flow,
                            run_id=f"bench-capture-{uuid.uuid4().hex}",
                            case_id=f"case-{uuid.uuid4().hex}",
                            storage_state=None,
                            headless=True,
                            run_mode="hybrid",
                        )

        stats, _ = await _measure_phase(cfg.iterations, cfg.warmup, _run_once)
        return stats

    minimal_stats = await _measure_policy(minimal)
    forensic_stats = await _measure_policy(forensic)
    report.phases["capture_policy_comparison"] = {
        "minimal": minimal_stats,
        "forensic": forensic_stats,
        "forensic_minus_minimal_p95_ms": forensic_stats["p95_ms"] - minimal_stats["p95_ms"],
    }


async def run_benchmark(cfg: BenchmarkConfig) -> BenchReport:
    from blop.engine.browser_session_manager import SESSION_MANAGER
    from blop.storage import sqlite
    from blop.tools import atomic_browser
    from blop.tools import context_read
    from blop.tools import resources
    from blop.tools import validate
    from blop.tools.regression import _run_and_persist, run_regression_test
    from blop.tools.release_check import run_release_check

    await sqlite.init_db()

    report = BenchReport(
        iterations=max(1, cfg.iterations),
        warmup=max(0, cfg.warmup),
    )
    seeded_flows = await _seed_benchmark_flows(BENCH_APP_URL, max(cfg.seed_flows, cfg.replay_flow_count))
    report.seeded_flow_count = len(seeded_flows)
    benchmark_flow_ids = [flow.flow_id for flow in seeded_flows[: cfg.replay_flow_count]]

    if not cfg.skip_context:
        context_read._workspace_cache = None  # type: ignore[attr-defined]
        t0 = time.perf_counter()
        r = await context_read.get_workspace_context(use_cache=False)
        cold_ws = time.perf_counter() - t0
        if not r.get("ok"):
            report.warnings.append(f"get_workspace_context cold returned ok=False: {r}")

        ws_stats, _ = await _measure_phase(
            report.iterations,
            0,
            lambda: context_read.get_workspace_context(),
        )

        context_read._ux_cache = None  # type: ignore[attr-defined]
        t0 = time.perf_counter()
        await context_read.get_ux_taxonomy(use_cache=False)
        cold_ux = time.perf_counter() - t0
        ux_stats, _ = await _measure_phase(report.iterations, 0, lambda: context_read.get_ux_taxonomy())
        journey_stats, journey_results = await _measure_phase(
            report.iterations,
            0,
            lambda: context_read.get_journeys_for_release(app_url=DEMOBLAZE_URL),
        )
        for result in journey_results:
            if not result.get("ok"):
                report.warnings.append(f"get_journeys_for_release not ok: {result.get('error', result)}")

        report.phases["context"] = {
            "get_workspace_context_cold_ms": cold_ws * 1000,
            "get_workspace_context_cached": ws_stats,
            "get_ux_taxonomy_cold_ms": cold_ux * 1000,
            "get_ux_taxonomy_cached": ux_stats,
            "get_journeys_for_release": journey_stats,
            "target_status": {
                "warm_context_reads": _target_status(ws_stats["p95_ms"], report.targets["warm_context_reads_p95_ms"]),
            },
        }

        resource_journeys_stats, _ = await _measure_phase(
            report.iterations,
            0,
            lambda: resources.journeys_resource(app_url=BENCH_APP_URL),
        )
        release_journeys_stats, _ = await _measure_phase(
            report.iterations,
            0,
            lambda: context_read.get_journeys_for_release(app_url=BENCH_APP_URL),
        )
        report.phases["resource_hotpaths"] = {
            "seeded_flow_count": len(seeded_flows),
            "journeys_resource": resource_journeys_stats,
            "get_journeys_for_release": release_journeys_stats,
            "target_status": {
                "warm_resource_reads": _target_status(
                    max(resource_journeys_stats["p95_ms"], release_journeys_stats["p95_ms"]),
                    report.targets["warm_resource_reads_p95_ms"],
                ),
            },
        }

    if cfg.validate:
        val_stats, _ = await _measure_phase(
            report.iterations,
            0,
            lambda: validate.validate_release_setup(app_url=DEMOBLAZE_URL, profile_name=None, check_mobile=False),
        )
        report.phases["validate_release_setup"] = val_stats

    if not cfg.skip_release:
        with patch("blop.config.check_llm_api_key", return_value=(True, "GOOGLE_API_KEY")):
            with patch("blop.tools.regression._spawn_background_task", side_effect=_discard_background_task):
                regression_stats, regression_results = await _measure_phase(
                    report.iterations,
                    report.warmup,
                    lambda: run_regression_test(
                        app_url=BENCH_APP_URL,
                        flow_ids=benchmark_flow_ids,
                        headless=True,
                        run_mode="hybrid",
                    ),
                )
                for result in regression_results:
                    if "error" in result:
                        report.warnings.append(f"run_regression_test startup error: {result['error']}")

        async def _run_release_startup():
            with patch(
                "blop.tools.regression.run_regression_test",
                new=AsyncMock(return_value={"run_id": f"bench-run-{uuid.uuid4().hex}", "status": "queued"}),
            ):
                return await run_release_check(
                    app_url=BENCH_APP_URL,
                    flow_ids=benchmark_flow_ids,
                    mode="replay",
                    release_id=f"bench-release-{uuid.uuid4().hex}",
                )

        release_stats, release_results = await _measure_phase(report.iterations, report.warmup, _run_release_startup)
        for result in release_results:
            if "error" in result:
                report.warnings.append(f"run_release_check startup error: {result['error']}")
        report.phases["release_startup"] = {
            "run_regression_test": regression_stats,
            "run_release_check": release_stats,
            "target_status": {
                "release_startup": _target_status(
                    max(regression_stats["p95_ms"], release_stats["p95_ms"]),
                    report.targets["release_startup_p95_ms"],
                ),
            },
        }

        async def _run_replay_overhead():
            from blop.schemas import FailureCase
            from blop.storage import files as file_store

            selected_flows = seeded_flows[: cfg.replay_flow_count]
            run_id = f"bench-replay-{uuid.uuid4().hex}"
            await sqlite.create_run(
                run_id=run_id,
                app_url=BENCH_APP_URL,
                profile_name=None,
                flow_ids=[flow.flow_id for flow in selected_flows],
                headless=True,
                artifacts_dir=file_store.artifacts_dir(run_id),
                run_mode="hybrid",
            )

            async def _fake_run_flows(**kwargs):
                return [
                    FailureCase(
                        case_id=f"{run_id}-case-{idx}",
                        run_id=run_id,
                        flow_id=flow.flow_id,
                        flow_name=flow.flow_name,
                        status="pass",
                        severity="none",
                        replay_mode="selector",
                        business_criticality=flow.business_criticality,
                    )
                    for idx, flow in enumerate(selected_flows)
                ]

            with patch("blop.tools.regression.regression_engine.run_flows", side_effect=_fake_run_flows):
                with patch(
                    "blop.tools.regression.classifier.classify_case",
                    new=AsyncMock(side_effect=lambda case, _app_url: case),
                ):
                    with patch(
                        "blop.tools.regression.classifier.classify_run",
                        new=AsyncMock(return_value={"next_actions": [], "severity_counts": {}, "failed_cases": []}),
                    ):
                        with patch("blop.tools.regression._refresh_linked_release_brief", new=AsyncMock(return_value=None)):
                            with patch("blop.storage.sqlite.save_risk_calibration_record", new=AsyncMock(return_value=None)):
                                return await _run_and_persist(
                                    run_id=run_id,
                                    flows=selected_flows,
                                    app_url=BENCH_APP_URL,
                                    storage_state=None,
                                    headless=True,
                                    run_mode="hybrid",
                                )

        replay_stats, _ = await _measure_phase(report.iterations, report.warmup, _run_replay_overhead)
        report.phases["multi_flow_replay_overhead"] = {
            "flow_count": len(benchmark_flow_ids),
            "persist_classify_finalize": replay_stats,
            "target_status": {
                "multi_flow_replay_overhead": _target_status(
                    replay_stats["p95_ms"],
                    report.targets["multi_flow_replay_overhead_p95_ms"],
                ),
            },
        }

        await _benchmark_capture_policy_comparison(report, cfg)

    if not cfg.skip_browser:
        nav_snap_times: list[float] = []
        try:
            for i in range(report.warmup + report.iterations):
                t0 = time.perf_counter()
                nav = await atomic_browser.navigate_to_url(DEMOBLAZE_URL)
                if not nav.get("ok"):
                    report.warnings.append(f"navigate_to_url failed: {nav}")
                    break
                snap = await atomic_browser.get_page_snapshot()
                if not snap.get("ok"):
                    report.warnings.append(f"get_page_snapshot failed: {snap}")
                    break
                elapsed = time.perf_counter() - t0
                if i >= report.warmup:
                    nav_snap_times.append(elapsed)
        except Exception as e:
            report.warnings.append(f"browser phase exception: {e}")
        finally:
            try:
                await SESSION_MANAGER.close()
            except Exception as e:
                report.warnings.append(f"SESSION_MANAGER.close(): {e}")

        report.phases["browser_navigate_plus_snapshot"] = _stats_ms(nav_snap_times)

    return report


def _print_report(report: BenchReport) -> None:
    print(f"Target: {report.target_url}")
    print(f"Seeded app URL: {report.seeded_app_url} ({report.seeded_flow_count} synthetic flows)")
    print(f"Iterations (timed): {report.iterations}, warmup discarded: {report.warmup}")
    print("Targets:")
    for name, target in report.targets.items():
        print(f"  {name}: <= {target:.0f}ms")
    for name, data in report.phases.items():
        print(f"\n## {name}")
        if isinstance(data, dict) and "mean_ms" in data:
            print(
                f"  n={int(data['n'])}  mean={data['mean_ms']:.2f}ms  p50={data['p50_ms']:.2f}ms  "
                f"p95={data['p95_ms']:.2f}ms  min={data['min_ms']:.2f}ms  max={data['max_ms']:.2f}ms"
            )
        else:
            for k, v in data.items():
                if isinstance(v, dict) and "mean_ms" in v:
                    print(
                        f"  {k}: n={int(v['n'])} mean={v['mean_ms']:.2f}ms p50={v['p50_ms']:.2f}ms "
                        f"p95={v['p95_ms']:.2f}ms"
                    )
                else:
                    if isinstance(v, (int, float)) and k.endswith("_ms"):
                        print(f"  {k}: {float(v):.3f}ms")
                    else:
                        print(f"  {k}: {v}")
    if report.warnings:
        print("\nWarnings:")
        for w in report.warnings:
            print(f"  - {w}")
    print("\nNote: in-process timings only; stdio MCP transport not measured.")


def _report_to_json(report: BenchReport) -> dict[str, Any]:
    return {
        "target_url": report.target_url,
        "seeded_app_url": report.seeded_app_url,
        "seeded_flow_count": report.seeded_flow_count,
        "phases": report.phases,
        "targets": report.targets,
        "warnings": report.warnings,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Benchmark MCP tool paths vs DemoBlaze")
    parser.add_argument("--iterations", type=int, default=15, help="Timed iterations per phase (after warmup)")
    parser.add_argument("--warmup", type=int, default=2, help="Warmup iterations to discard")
    parser.add_argument("--skip-browser", action="store_true", help="Skip Playwright navigate + snapshot")
    parser.add_argument("--skip-context", action="store_true", help="Skip context/resource tool timing")
    parser.add_argument("--skip-release", action="store_true", help="Skip release startup and replay overhead timing")
    parser.add_argument("--validate", action="store_true", help="Include validate_release_setup(app_url) timing")
    parser.add_argument("--seed-flows", type=int, default=60, help="Number of synthetic flows to seed for hotpath benchmarks")
    parser.add_argument("--replay-flow-count", type=int, default=8, help="Number of seeded flows to use for replay-startup benchmarks")
    parser.add_argument("--json", action="store_true", help="Print JSON only")
    args = parser.parse_args()

    cfg = BenchmarkConfig(
        iterations=args.iterations,
        warmup=args.warmup,
        skip_browser=args.skip_browser,
        skip_context=args.skip_context,
        skip_release=args.skip_release,
        validate=args.validate,
        seed_flows=args.seed_flows,
        replay_flow_count=args.replay_flow_count,
        json_out=args.json,
    )
    report = asyncio.run(run_benchmark(cfg))
    if cfg.json_out:
        print(json.dumps(_report_to_json(report), indent=2))
    else:
        _print_report(report)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
