#!/usr/bin/env python3
"""
Smoke test: blop-mcp → Blop Cloud sync loop.

Usage:
    cd blop-mcp
    uv run python scripts/smoke_test_mcp_cloud_loop.py \\
        --url https://example.com \\
        --project-id <uuid> \\
        --hosted-url https://app.blop.dev \\
        --api-token blop_sk_...

Steps:
1. Validate cloud connection (probe_connection).
2. Run a minimal release check against --url.
3. Poll until the run completes (max 5 min).
4. Verify the cloud is reachable via GET /api/v1/sync/connection.
5. Assert: decision in (SHIP, INVESTIGATE, BLOCK).

Exit 0 on success, 1 on any assertion failure or timeout.
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
import time

import httpx


async def _poll_run(run_id: str, timeout: int = 300) -> dict:
    """Poll get_test_results until run is terminal."""
    from blop.config import GET_TEST_RESULTS_POLL_TERMINAL_STATUSES
    from blop.tools.results import get_test_results

    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        result = await get_test_results(run_id=run_id)
        status = result.get("status")
        if status in GET_TEST_RESULTS_POLL_TERMINAL_STATUSES:
            return result
        print(f"  run {run_id}: status={status} — waiting…")
        await asyncio.sleep(10)
    raise TimeoutError(f"Run {run_id} did not complete within {timeout}s")


async def smoke_test(
    url: str,
    project_id: str,
    hosted_url: str,
    api_token: str,
) -> None:
    from blop.storage import sqlite
    from blop.sync.client import SyncClient

    await sqlite.init_db()

    client = SyncClient(hosted_url=hosted_url, api_token=api_token)

    # ── Step 1: Probe connection ─────────────────────────────────────────────
    print("1. Probing cloud connection…")
    ok = await client.probe_connection(project_id=project_id)
    assert ok, "Cloud probe_connection returned False — check BLOP_HOSTED_URL / BLOP_API_TOKEN"
    print("   OK")

    # ── Step 2: Run release check ────────────────────────────────────────────
    print(f"2. Starting release check against {url}…")
    os.environ["BLOP_HOSTED_URL"] = hosted_url
    os.environ["BLOP_API_TOKEN"] = api_token
    os.environ["BLOP_PROJECT_ID"] = project_id

    # Import after env vars are set so config picks them up
    from blop.tools.release_check import run_release_check  # noqa: PLC0415

    result = await run_release_check(app_url=url, headless=True)
    run_id = result["run_id"]
    release_id = result.get("release_id")
    print(f"   run_id={run_id}  release_id={release_id}")

    # ── Step 3: Poll until terminal ──────────────────────────────────────────
    print("3. Polling until run completes (max 5 min)…")
    final = await _poll_run(run_id)
    status = final["status"]
    decision = final.get("decision", "INVESTIGATE")
    cases = final.get("cases", [])
    print(f"   status={status}  decision={decision}  cases={len(cases)}")
    assert status in ("completed", "failed"), (
        f"Smoke expects a finished replay (completed or failed); got {status!r}. "
        "If you see cancelled, interrupted, or waiting_auth, fix the environment and re-run."
    )

    # ── Step 4: Verify cloud is reachable ────────────────────────────────────
    print("4. Verifying cloud connection endpoint…")
    await asyncio.sleep(5)  # allow async sync to propagate

    async with httpx.AsyncClient(timeout=10) as http:
        resp = await http.get(
            f"{hosted_url.rstrip('/')}/api/v1/sync/connection",
            headers={"Authorization": f"Bearer {api_token}"},
        )
    assert resp.status_code == 200, f"Cloud connection check failed: {resp.status_code}"
    print("   OK")

    # ── Step 5: Assert decision ──────────────────────────────────────────────
    print("5. Asserting decision…")
    assert decision in ("SHIP", "INVESTIGATE", "BLOCK"), f"Invalid decision: {decision!r}"
    print(f"   decision={decision}  cases={len(cases)}")

    print("\nSMOKE TEST PASSED")


def main() -> None:
    parser = argparse.ArgumentParser(description="blop-mcp → Blop Cloud smoke test")
    parser.add_argument("--url", required=True, help="App URL to test (e.g. https://example.com)")
    parser.add_argument("--project-id", required=True, help="Blop Cloud project UUID")
    parser.add_argument("--hosted-url", required=True, help="Blop Cloud base URL")
    parser.add_argument("--api-token", required=True, help="Blop Cloud API token (blop_sk_…)")
    args = parser.parse_args()

    try:
        asyncio.run(
            smoke_test(
                url=args.url,
                project_id=args.project_id,
                hosted_url=args.hosted_url,
                api_token=args.api_token,
            )
        )
    except (AssertionError, TimeoutError) as exc:
        print(f"\nSMOKE TEST FAILED: {exc}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
