#!/usr/bin/env python3
"""MCP stdio startup probe plus real tool execution for DemoBlaze."""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import time
from pathlib import Path

from _demoblaze_bench_lib import DEMOBLAZE_URL, ROOT, ensure_src_on_path, print_json, repo_pythonpath


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", default=DEMOBLAZE_URL)
    parser.add_argument("--skip-browser", action="store_true")
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--inner-tools", action="store_true")
    return parser.parse_args()


def _probe_stdio_startup() -> float:
    started = time.perf_counter()
    proc = subprocess.Popen(
        ["uv", "run", "blop-mcp"],
        cwd=str(ROOT),
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env=repo_pythonpath(os.environ.copy()),
    )
    try:
        time.sleep(0.25)
        return time.perf_counter() - started
    finally:
        if proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait(timeout=5)


def _run_inner(args: argparse.Namespace) -> dict:
    ensure_src_on_path()
    import asyncio

    async def _main() -> dict:
        from blop.tools.atomic_browser import get_page_snapshot, navigate_to_url
        from blop.tools.context_read import get_workspace_context

        started = time.perf_counter()
        workspace = await get_workspace_context()
        workspace_s = time.perf_counter() - started

        results = {"get_workspace_context": workspace}
        timings_s = {"get_workspace_context_s": workspace_s}

        if not args.skip_browser:
            started = time.perf_counter()
            nav = await navigate_to_url(args.url)
            nav_s = time.perf_counter() - started
            started = time.perf_counter()
            snap = await get_page_snapshot()
            snap_s = time.perf_counter() - started
            results["navigate_to_url"] = nav
            results["get_page_snapshot"] = snap
            timings_s["navigate_to_url_s"] = nav_s
            timings_s["get_page_snapshot_s"] = snap_s

        return {
            "ok": True,
            "target_url": args.url,
            "skip_browser": args.skip_browser,
            "timings_s": timings_s,
            "results": results,
        }

    return asyncio.run(_main())


def _run_outer(args: argparse.Namespace) -> dict:
    initialize_s = _probe_stdio_startup()
    cmd = [
        "uv",
        "run",
        "python3",
        str(Path(__file__).resolve()),
        "--inner-tools",
        "--url",
        args.url,
        "--json",
    ]
    if args.skip_browser:
        cmd.append("--skip-browser")
    proc = subprocess.run(
        cmd,
        cwd=str(ROOT),
        capture_output=True,
        text=True,
        timeout=300,
        env=repo_pythonpath(os.environ.copy()),
    )
    if proc.returncode != 0:
        raise RuntimeError(proc.stderr.strip() or proc.stdout.strip() or "inner tool probe failed")
    inner = json.loads(proc.stdout)
    inner.setdefault("timings_s", {})
    inner["timings_s"]["initialize_s"] = initialize_s
    return inner


def main() -> int:
    args = parse_args()
    payload = _run_inner(args) if args.inner_tools else _run_outer(args)
    if args.json:
        print_json(payload)
    else:
        print(json.dumps(payload, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
