#!/usr/bin/env python3
"""Live DemoBlaze benchmark for canonical workflow and MCP hotpaths."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import time
from pathlib import Path
from typing import Any
from unittest.mock import patch

from _demoblaze_bench_lib import (
    DEFAULT_ITERATIONS,
    DEMOBLAZE_URL,
    FLOW_TARGETS,
    ROOT,
    aggregate_phase_maps,
    build_iteration_table,
    build_verdicts,
    compact_summary,
    ensure_src_on_path,
    make_temp_paths,
    metric,
    poll_run,
    print_json,
    repo_pythonpath,
    run_json_subprocess,
    seed_flows,
    timed_call,
    utc_now,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--target-url", default=DEMOBLAZE_URL)
    parser.add_argument("--iterations", type=int, default=DEFAULT_ITERATIONS)
    parser.add_argument("--skip-browser", action="store_true")
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--single-iteration", type=int)
    parser.add_argument("--iteration-root")
    parser.add_argument("--output")
    parser.add_argument("--replay-timeout-secs", type=int, default=300)
    return parser.parse_args()


def _script_env(iteration_root: str, *, base_env: dict[str, str] | None = None) -> dict[str, str]:
    env = repo_pythonpath(base_env)
    env["BLOP_DB_PATH"] = str(Path(iteration_root) / "bench.db")
    env["BLOP_RUNS_DIR"] = str(Path(iteration_root) / "runs")
    env.setdefault("BLOP_TARGETED_MAX_STEPS", "12")
    env.setdefault("BLOP_MAX_STEPS", "25")
    return env


def _run_stdio_probe(target_url: str, *, skip_browser: bool, env: dict[str, str]) -> dict[str, Any]:
    cmd = [sys.executable, str(ROOT / "scripts" / "mcp_stdio_e2e_demoblaze.py"), "--url", target_url, "--json"]
    if skip_browser:
        cmd.append("--skip-browser")
    proc = run_json_subprocess(cmd, env=env, cwd=ROOT, timeout=240)
    data = dict(proc.data)
    data["returncode"] = proc.returncode
    return data


def _summarize_stdio_phase(stdio: dict[str, Any]) -> dict[str, Any]:
    timings = stdio.get("timings_s", {}) or {}
    results = stdio.get("results", {}) or {}
    phases = {
        "initialize": metric(timings.get("initialize_s"), stdio.get("ok", False)),
        "get_workspace_context": metric(
            timings.get("get_workspace_context_s"),
            bool((results.get("get_workspace_context") or {}).get("ok", True)),
        ),
    }
    if "navigate_to_url_s" in timings:
        phases["navigate_to_url"] = metric(
            timings.get("navigate_to_url_s"),
            bool((results.get("navigate_to_url") or {}).get("ok", True)),
        )
    if "get_page_snapshot_s" in timings:
        snapshot = results.get("get_page_snapshot") or {}
        node_count = None
        if isinstance(snapshot, dict):
            node_count = ((snapshot.get("data") or {}).get("node_count")) if snapshot.get("data") else None
        phases["get_page_snapshot"] = metric(
            timings.get("get_page_snapshot_s"),
            bool(snapshot.get("ok", True) if isinstance(snapshot, dict) else True),
            node_count=node_count,
        )
    return phases


def _record_environment_blocker(reason: str, *, iteration: int, target_url: str, temperature: str) -> dict[str, Any]:
    return {
        "iteration": iteration,
        "temperature": temperature,
        "timestamp": utc_now(),
        "target_url": target_url,
        "workflow": {
            "ok": False,
            "status": "blocked",
            "environment_blocker": reason,
            "recorded_flow_count": 0,
            "navigation_metrics": [],
            "total_seconds": 0.0,
        },
        "phases": {
            "canonical_workflow": {},
            "capture_policy_comparison": {
                "default_vs_alt_policy": metric(None, False, skipped=True, reason=reason),
            },
        },
    }


async def _run_skip_browser_iteration(iteration: int, target_url: str, env: dict[str, str]) -> dict[str, Any]:
    ensure_src_on_path()
    from blop.storage.sqlite import init_db
    from blop.tools import context_read
    from blop.tools.regression import run_regression_test
    from blop.tools.release_check import run_release_check
    from blop.tools.resources import journeys_resource

    await init_db()
    flow_ids = await seed_flows(target_url, 12)

    stdio = _run_stdio_probe(target_url, skip_browser=True, env=env)

    phases: dict[str, dict[str, Any]] = {
        "mcp_stdio": _summarize_stdio_phase(stdio),
    }

    elapsed, workspace = await timed_call(context_read.get_workspace_context, False)
    elapsed_journeys, journeys = await timed_call(journeys_resource, target_url)
    elapsed_release, release_journeys = await timed_call(context_read.get_journeys_for_release, None, target_url, False)
    phases["context"] = {
        "get_workspace_context": metric(elapsed, bool(workspace.get("ok", True))),
        "get_journeys_for_release": metric(elapsed_release, bool(release_journeys.get("ok", True))),
    }
    phases["resource_hotpaths"] = {
        "journeys_resource": metric(elapsed_journeys, "journeys" in journeys, total=journeys.get("total")),
    }

    def _done_future():
        loop = asyncio.get_running_loop()
        fut = loop.create_future()
        fut.set_result(None)
        return fut

    def _discard_background_task(coro):
        if asyncio.iscoroutine(coro):
            coro.close()
        return _done_future()

    with patch("blop.config.check_llm_api_key", return_value=(True, "GOOGLE_API_KEY")):
        with patch("blop.tools.regression._spawn_background_task", side_effect=_discard_background_task):
            elapsed_regression, regression_result = await timed_call(
                run_regression_test,
                app_url=target_url,
                flow_ids=flow_ids,
                headless=True,
                run_mode="hybrid",
            )
            elapsed_release_start, release_result = await timed_call(
                run_release_check,
                app_url=target_url,
                flow_ids=flow_ids,
                headless=True,
                mode="replay",
            )
            elapsed_one_flow, one_flow_result = await timed_call(
                run_regression_test,
                app_url=target_url,
                flow_ids=flow_ids[:1],
                headless=True,
                run_mode="hybrid",
            )

    phases["release_startup"] = {
        "run_regression_test": metric(elapsed_regression, regression_result.get("status") == "queued"),
        "run_release_check": metric(elapsed_release_start, release_result.get("status") == "queued"),
    }
    phases["multi_flow_replay_overhead"] = {
        "single_flow_queue_start": metric(elapsed_one_flow, one_flow_result.get("status") == "queued", flow_count=1),
        "multi_flow_queue_start": metric(
            elapsed_regression,
            regression_result.get("status") == "queued",
            flow_count=len(flow_ids),
        ),
    }
    phases["capture_policy_comparison"] = {
        "default_vs_alt_policy": metric(
            None,
            True,
            skipped=True,
            reason="Skip-browser mode does not mutate evidence policy or collect live artifact comparisons.",
        )
    }

    return {
        "iteration": iteration,
        "temperature": "cold" if iteration == 1 else "warm",
        "timestamp": utc_now(),
        "target_url": target_url,
        "skip_browser": True,
        "flow_ids_seeded": flow_ids,
        "stdio": stdio,
        "workflow": {
            "ok": True,
            "status": "seeded_only",
            "decision": None,
            "recorded_flow_count": len(flow_ids),
            "navigation_metrics": [],
            "total_seconds": elapsed + elapsed_journeys + elapsed_release + elapsed_regression + elapsed_release_start,
        },
        "phases": phases,
    }


async def _run_live_iteration(
    iteration: int,
    target_url: str,
    env: dict[str, str],
    *,
    replay_timeout_secs: int,
) -> dict[str, Any]:
    ensure_src_on_path()
    from blop.storage.sqlite import init_db
    from blop.tools.context_read import get_journeys_for_release, get_workspace_context
    from blop.tools.journeys import discover_critical_journeys
    from blop.tools.record import record_test_flow
    from blop.tools.release_check import run_release_check
    from blop.tools.resources import journeys_resource
    from blop.tools.results import get_run_health_stream, get_test_results
    from blop.tools.triage import triage_release_blocker
    from blop.tools.validate import validate_release_setup

    await init_db()

    stdio = _run_stdio_probe(target_url, skip_browser=False, env=env)
    phases: dict[str, dict[str, Any]] = {
        "mcp_stdio": _summarize_stdio_phase(stdio),
    }
    if "navigate_to_url" in phases["mcp_stdio"] or "get_page_snapshot" in phases["mcp_stdio"]:
        phases["atomic_browser"] = {
            key: value for key, value in phases["mcp_stdio"].items() if key in {"navigate_to_url", "get_page_snapshot"}
        }

    workflow_started = time.perf_counter()

    validate_s, validate_result = await timed_call(validate_release_setup, app_url=target_url)
    phases["canonical_workflow"] = {
        "validate_release_setup": metric(validate_s, validate_result.get("status") in {"ready", "warnings"}),
    }
    if validate_result.get("status") == "blocked":
        reason = "; ".join(validate_result.get("blockers", [])) or "validate_release_setup returned blocked"
        blocked = _record_environment_blocker(
            reason, iteration=iteration, target_url=target_url, temperature="cold" if iteration == 1 else "warm"
        )
        blocked["phases"]["mcp_stdio"] = phases["mcp_stdio"]
        if "atomic_browser" in phases:
            blocked["phases"]["atomic_browser"] = phases["atomic_browser"]
        blocked["phases"]["canonical_workflow"]["validate_release_setup"] = phases["canonical_workflow"][
            "validate_release_setup"
        ]
        return blocked

    workspace_s, workspace = await timed_call(get_workspace_context)
    resource_s, resource = await timed_call(journeys_resource, target_url)
    release_journeys_s, release_journeys = await timed_call(get_journeys_for_release, None, target_url)
    phases["context"] = {
        "get_workspace_context": metric(workspace_s, bool(workspace.get("ok", True))),
        "get_journeys_for_release": metric(release_journeys_s, bool(release_journeys.get("ok", True))),
    }
    phases["resource_hotpaths"] = {
        "journeys_resource": metric(resource_s, "journeys" in resource, total=resource.get("total")),
    }

    discovery_s, discovery = await timed_call(
        discover_critical_journeys,
        app_url=target_url,
        business_goal="E-commerce storefront with release-critical catalog, cart, and checkout journeys.",
        max_depth=2,
        max_pages=8,
    )
    phases["canonical_workflow"]["discover_critical_journeys"] = metric(
        discovery_s,
        "journeys" in discovery,
        journey_count=discovery.get("journey_count"),
        release_gating_count=discovery.get("release_gating_count"),
    )

    record_metrics: dict[str, Any] = {}
    flow_ids: list[str] = []
    record_outputs: list[dict[str, Any]] = []
    for target in FLOW_TARGETS:
        elapsed, recorded = await timed_call(
            record_test_flow,
            app_url=target_url,
            flow_name=target["flow_name"],
            goal=target["goal"],
            business_criticality=target["business_criticality"],
        )
        ok = "flow_id" in recorded and recorded.get("status") == "recorded"
        record_metrics[target["flow_name"]] = metric(
            elapsed,
            ok,
            flow_id=recorded.get("flow_id"),
            step_count=recorded.get("step_count"),
        )
        record_outputs.append(recorded)
        if recorded.get("flow_id"):
            flow_ids.append(recorded["flow_id"])

    phases["canonical_workflow"]["record_test_flow_checkout"] = record_metrics[FLOW_TARGETS[0]["flow_name"]]
    phases["canonical_workflow"]["record_test_flow_activation"] = record_metrics[FLOW_TARGETS[1]["flow_name"]]
    phases["canonical_workflow"]["record_test_flow_cart_management"] = record_metrics[FLOW_TARGETS[2]["flow_name"]]

    start_release_s, release_start = await timed_call(
        run_release_check,
        app_url=target_url,
        flow_ids=flow_ids,
        mode="replay",
        headless=True,
    )
    phases["release_startup"] = {
        "run_release_check": metric(start_release_s, release_start.get("status") in {"queued", "running"}),
    }

    run_id = release_start.get("run_id")
    release_id = release_start.get("release_id")
    if not run_id:
        reason = release_start.get("error", "run_release_check did not return a run_id")
        blocked = _record_environment_blocker(
            reason, iteration=iteration, target_url=target_url, temperature="cold" if iteration == 1 else "warm"
        )
        blocked["phases"].update(phases)
        return blocked

    replay_started = time.perf_counter()
    report = await poll_run(get_test_results, run_id, timeout_secs=replay_timeout_secs)
    replay_terminal_s = time.perf_counter() - replay_started
    phases["canonical_workflow"]["replay_to_terminal"] = metric(
        replay_terminal_s,
        report.get("status") in {"completed", "failed"},
        status=report.get("status"),
    )

    health_s, health = await timed_call(get_run_health_stream, run_id, 500)
    phases["canonical_workflow"]["get_run_health_stream"] = metric(
        health_s, "events" in health, event_count=health.get("event_count")
    )

    triage_output: dict[str, Any] | None = None
    if report.get("release_recommendation", {}).get("decision") != "SHIP":
        triage_s, triage_output = await timed_call(triage_release_blocker, run_id=run_id)
        phases["canonical_workflow"]["triage_release_blocker"] = metric(
            triage_s,
            bool(triage_output and "likely_cause" in triage_output),
        )
    else:
        phases["canonical_workflow"]["triage_release_blocker"] = metric(
            None,
            True,
            skipped=True,
            reason="Decision was SHIP; blocker triage was not required.",
        )

    phases["multi_flow_replay_overhead"] = {
        "release_queue_start": metric(
            start_release_s,
            release_start.get("status") in {"queued", "running"},
            flow_count=len(flow_ids),
        ),
        "queue_to_terminal": metric(
            replay_terminal_s,
            report.get("status") in {"completed", "failed"},
            flow_count=len(flow_ids),
        ),
    }
    phases["capture_policy_comparison"] = {
        "default_vs_alt_policy": metric(
            None,
            True,
            skipped=True,
            reason="The live benchmark keeps the default evidence policy to stay faithful to canonical workflow.",
        )
    }

    navigation_metrics: list[dict[str, Any]] = []
    artifact_count = 0
    failure_buckets: list[str] = []
    for case in report.get("cases", []) or []:
        if isinstance(case, dict):
            navigation_metrics.extend(case.get("performance_metrics", []) or [])
            artifact_count += len(case.get("screenshots", []) or [])
            bucket = case.get("stability_bucket")
            if bucket:
                failure_buckets.append(bucket)

    replay_step_elapsed_ms = [
        event.get("payload", {}).get("elapsed_ms")
        for event in health.get("events", []) or []
        if event.get("event_type") == "replay_step_completed" and event.get("payload", {}).get("elapsed_ms") is not None
    ]

    workflow_total_s = time.perf_counter() - workflow_started
    decision = report.get("release_recommendation", {}).get("decision")
    return {
        "iteration": iteration,
        "temperature": "cold" if iteration == 1 else "warm",
        "timestamp": utc_now(),
        "target_url": target_url,
        "skip_browser": False,
        "stdio": stdio,
        "discovery": {
            "journey_count": discovery.get("journey_count"),
            "release_gating_count": discovery.get("release_gating_count"),
            "recommended_flow_ids": discovery.get("recommended_flow_ids"),
        },
        "workflow": {
            "ok": report.get("status") in {"completed", "failed"},
            "status": report.get("status"),
            "decision": decision,
            "top_failure_mode": report.get("top_failure_mode"),
            "stability_bucket": report.get("stability_bucket"),
            "environment_blocker": None,
            "run_id": run_id,
            "release_id": release_id,
            "recorded_flow_count": len(flow_ids),
            "artifact_count": artifact_count,
            "failure_buckets": sorted(set(failure_buckets)),
            "replay_step_elapsed_ms": replay_step_elapsed_ms,
            "navigation_metrics": navigation_metrics,
            "total_seconds": workflow_total_s,
        },
        "reports": {
            "validate_release_setup": validate_result,
            "discover_critical_journeys": discovery,
            "record_test_flow": record_outputs,
            "run_release_check": release_start,
            "get_test_results": report,
            "get_run_health_stream": health,
            "triage_release_blocker": triage_output,
        },
        "phases": phases,
    }


async def run_single_iteration(args: argparse.Namespace) -> dict[str, Any]:
    if not args.iteration_root:
        raise SystemExit("--iteration-root is required with --single-iteration")
    env = _script_env(args.iteration_root, base_env=os.environ.copy())
    os.environ.update(env)
    if args.skip_browser:
        return await _run_skip_browser_iteration(args.single_iteration, args.target_url, env)
    return await _run_live_iteration(
        args.single_iteration,
        args.target_url,
        env,
        replay_timeout_secs=args.replay_timeout_secs,
    )


def _write_output(path: str | None, payload: dict[str, Any]) -> None:
    if not path:
        return
    Path(path).write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")


def _aggregate_parent(args: argparse.Namespace) -> dict[str, Any]:
    iteration_payloads: list[dict[str, Any]] = []
    for iteration in range(1, args.iterations + 1):
        paths = make_temp_paths(iteration)
        cmd = [
            "uv",
            "run",
            "python3",
            str(Path(__file__).resolve()),
            "--single-iteration",
            str(iteration),
            "--iteration-root",
            paths["root"],
            "--target-url",
            args.target_url,
            "--replay-timeout-secs",
            str(args.replay_timeout_secs),
            "--json",
        ]
        if args.skip_browser:
            cmd.append("--skip-browser")
        env = repo_pythonpath(os.environ.copy())
        child = run_json_subprocess(cmd, env=env, cwd=ROOT, timeout=max(240, args.replay_timeout_secs + 180))
        payload = child.data
        if not payload:
            payload = _record_environment_blocker(
                f"Child iteration produced no JSON output (returncode={child.returncode})",
                iteration=iteration,
                target_url=args.target_url,
                temperature="cold" if iteration == 1 else "warm",
            )
        payload["child_returncode"] = child.returncode
        _write_output(paths["iteration_json"], payload)
        payload["artifact_paths"] = paths
        iteration_payloads.append(payload)

    aggregated = aggregate_phase_maps(iteration_payloads)
    verdicts = build_verdicts(aggregated, iteration_payloads)
    return {
        "target_url": args.target_url,
        "iterations": args.iterations,
        "generated_at": utc_now(),
        "skip_browser": args.skip_browser,
        "targets": {
            "primary_app_url": args.target_url,
            "stdio_probe": str(ROOT / "scripts" / "mcp_stdio_e2e_demoblaze.py"),
            "benchmark_flows": FLOW_TARGETS,
        },
        "iteration_table": build_iteration_table(iteration_payloads),
        "iteration_results": iteration_payloads,
        "phases": {
            phase: {name: compact_summary(summary) for name, summary in metrics.items()}
            for phase, metrics in aggregated.items()
        },
        "verdicts": verdicts,
    }


def main() -> int:
    args = parse_args()
    if args.single_iteration:
        try:
            payload = asyncio.run(run_single_iteration(args))
        except ModuleNotFoundError as exc:
            missing = getattr(exc, "name", None) or str(exc)
            blocker = _record_environment_blocker(
                f"Missing runtime dependency in current interpreter: {missing}. Re-run with `uv run`.",
                iteration=args.single_iteration,
                target_url=args.target_url,
                temperature="cold" if args.single_iteration == 1 else "warm",
            )
            _write_output(args.output, blocker)
            if args.json:
                print_json(blocker)
            else:
                print(json.dumps(blocker, indent=2, sort_keys=True))
            return 0
        _write_output(args.output, payload)
        if args.json:
            print_json(payload)
        else:
            print(json.dumps(payload, indent=2, sort_keys=True))
        return 0

    payload = _aggregate_parent(args)
    _write_output(args.output, payload)
    if args.json:
        print_json(payload)
    else:
        print(json.dumps(payload, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
