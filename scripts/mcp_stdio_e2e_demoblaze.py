#!/usr/bin/env python3
"""
Real-time MCP test: spawn blop-mcp over stdio and call tools like an IDE agent would.

Measures wall time per tools/call (transport + handler). Requires Chromium + network.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import time
from datetime import timedelta
from pathlib import Path
from typing import Any

_REPO_ROOT = Path(__file__).resolve().parent.parent

try:
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client
    from mcp.types import CallToolResult
except ImportError as e:
    print("mcp package required: pip install mcp", file=sys.stderr)
    raise SystemExit(1) from e


def _parse_tool_result(result: CallToolResult) -> dict[str, Any]:
    if result.structuredContent is not None:
        return dict(result.structuredContent)
    for block in result.content:
        text = getattr(block, "text", None)
        if text:
            return json.loads(text)
    raise ValueError(f"Unparseable tool result: {result}")


async def _run_chain(
    *,
    url: str,
    tool_timeout_s: int,
    skip_browser: bool,
    click_ref: str | None,
) -> dict[str, Any]:
    params = StdioServerParameters(
        command="uv",
        args=["run", "blop-mcp"],
        cwd=str(_REPO_ROOT),
        env={**os.environ},
    )
    timeout = timedelta(seconds=tool_timeout_s)
    timings: dict[str, float] = {}
    results: dict[str, Any] = {}

    t_session = time.perf_counter()
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            t0 = time.perf_counter()
            await session.initialize()
            timings["initialize_s"] = time.perf_counter() - t0

            async def _call(
                name: str,
                arguments: dict[str, Any],
                *,
                timing_label: str | None = None,
            ) -> dict[str, Any]:
                t0 = time.perf_counter()
                r = await session.call_tool(name, arguments, read_timeout_seconds=timeout)
                label = timing_label or name
                timings[f"tool_{label}_s"] = time.perf_counter() - t0
                if r.isError:
                    parts = [getattr(b, "text", str(b)) for b in r.content]
                    raise RuntimeError(f"tool {name} error: {' | '.join(parts)}")
                return _parse_tool_result(r)

            results["get_workspace_context"] = await _call("get_workspace_context", {})
            if not skip_browser:
                results["navigate_to_url"] = await _call("navigate_to_url", {"url": url})
                results["get_page_snapshot"] = await _call("get_page_snapshot", {})
                if click_ref:
                    results["perform_step"] = await _call(
                        "perform_step",
                        {"step_spec": {"action": "click", "ref": click_ref}},
                    )
                    results["get_page_snapshot_after_click"] = await _call(
                        "get_page_snapshot",
                        {},
                        timing_label="get_page_snapshot_after_click",
                    )

    timings["total_session_s"] = time.perf_counter() - t_session
    return {"url": url, "timings_s": timings, "results": results}


def main() -> int:
    parser = argparse.ArgumentParser(
        description="E2E: stdio MCP client → blop-mcp → context + browser tools",
    )
    parser.add_argument(
        "--url",
        default="https://www.demoblaze.com",
        help="Target URL for navigate_to_url",
    )
    parser.add_argument(
        "--tool-timeout",
        type=int,
        default=120,
        help="Per-tool read timeout (seconds)",
    )
    parser.add_argument(
        "--skip-browser",
        action="store_true",
        help="Only call get_workspace_context (no Playwright)",
    )
    parser.add_argument(
        "--click-ref",
        default=None,
        help="After snapshot, perform_step click with this ref (e.g. e3 for Contact)",
    )
    parser.add_argument("--json", action="store_true", help="Print one JSON object to stdout")
    args = parser.parse_args()

    try:
        payload = asyncio.run(
            _run_chain(
                url=args.url,
                tool_timeout_s=args.tool_timeout,
                skip_browser=args.skip_browser,
                click_ref=args.click_ref,
            )
        )
    except Exception as e:
        print(f"E2E failed: {e}", file=sys.stderr)
        return 1

    if args.json:
        print(json.dumps(payload, indent=2))
    else:
        print(f"Target: {payload['url']}")
        for k, v in payload["timings_s"].items():
            print(f"  {k}: {v:.3f}s")
        nav = payload["results"].get("navigate_to_url", {})
        if nav.get("ok") and nav.get("data"):
            print(f"  landed: {nav['data'].get('title', '')!r} @ {nav['data'].get('url', '')}")
        snap = payload["results"].get("get_page_snapshot", {})
        if snap.get("ok") and snap.get("data"):
            print(f"  snapshot nodes: {snap['data'].get('node_count')}")
        print("OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
