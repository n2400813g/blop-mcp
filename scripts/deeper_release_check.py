#!/usr/bin/env python3
"""Deeper than validate_release_setup: agent evaluation, journey discovery, optional replay.

Loads repo-root ``.env`` via ``blop.config`` (same as ``production_mcp_smoke.py``).

Typical use (from repo root, with ``APP_BASE_URL`` and LLM key set)::

    uv run python scripts/deeper_release_check.py
    uv run python scripts/deeper_release_check.py --task "Summarize the homepage value prop and try the primary CTA."
    BLOP_DEEPER_CHECK_TASK="..." uv run python scripts/deeper_release_check.py

Stages (comma-separated ``--stages``):

- ``preflight`` — ``validate_release_setup`` (fails fast if blocked).
- ``eval`` — ``evaluate_web_task`` (one-shot browser agent + screenshots/console/network).
- ``discover`` — ``discover_critical_journeys`` (crawl + LLM journey plan).
- ``release`` — ``run_release_check`` replay + poll (only if recorded flows exist for the app).

``release`` is skipped with a clear message when no matching flows are in the local DB.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import time
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT / "src") not in sys.path:
    sys.path.insert(0, str(_ROOT / "src"))


def _parse_stages(raw: str) -> list[str]:
    parts = [p.strip().lower() for p in raw.split(",") if p.strip()]
    allowed = {"preflight", "eval", "discover", "release"}
    bad = [p for p in parts if p not in allowed]
    if bad:
        raise SystemExit(f"Unknown stage(s): {bad}. Allowed: {sorted(allowed)}")
    return parts


def _print_json(obj: dict) -> None:
    print(json.dumps(obj, indent=2, default=str))


async def _run(args: argparse.Namespace) -> int:
    import blop.config  # noqa: F401 — loads .env
    from blop.config import GET_TEST_RESULTS_POLL_TERMINAL_STATUSES
    from blop.tools.evaluate import evaluate_web_task
    from blop.tools.journeys import discover_critical_journeys
    from blop.tools.release_check import run_release_check
    from blop.tools.results import get_test_results
    from blop.tools.validate import validate_release_setup

    app_url = (os.environ.get("APP_BASE_URL") or os.environ.get("BLOP_APP_URL") or "").strip()
    if not app_url:
        print("FAIL: set APP_BASE_URL or BLOP_APP_URL in .env", file=sys.stderr)
        return 2

    stages = _parse_stages(args.stages)

    if "preflight" in stages:
        print("== preflight: validate_release_setup ==")
        vr = await validate_release_setup(app_url=app_url, profile_name=args.profile or None)
        if vr.get("status") == "blocked":
            _print_json(
                {
                    "stage": "preflight",
                    "status": vr.get("status"),
                    "blockers": vr.get("blockers"),
                    "checks": [{"name": c.get("name"), "passed": c.get("passed")} for c in vr.get("checks", [])],
                }
            )
            return 1
        print("preflight:", vr.get("status"), "-", vr.get("headline", ""))

    if "eval" in stages:
        print("== eval: evaluate_web_task ==")
        task = (args.task or os.environ.get("BLOP_DEEPER_CHECK_TASK") or "").strip()
        if not task:
            task = (
                "Open the app, skim the main landing content, list primary navigation or section labels, "
                "click the most prominent non-destructive CTA if one exists, and report console errors "
                "or failed network requests you observe."
            )
        ev = await evaluate_web_task(
            task=task,
            app_url=app_url,
            profile_name=args.profile or None,
            headless=args.headless,
            max_steps=args.eval_max_steps,
            format="markdown",
        )
        if isinstance(ev, dict) and ev.get("error"):
            print("FAIL [eval]:", ev.get("error"), file=sys.stderr)
            return 1
        summary = {
            "stage": "eval",
            "run_id": ev.get("run_id"),
            "pass_fail": ev.get("pass_fail"),
            "release_recommendation": ev.get("release_recommendation"),
        }
        _print_json(summary)
        if args.print_report and ev.get("formatted_report"):
            print(ev["formatted_report"])

    if "discover" in stages:
        print("== discover: discover_critical_journeys ==")
        dj = await discover_critical_journeys(
            app_url=app_url,
            profile_name=args.profile or None,
            max_depth=args.max_depth,
            max_pages=args.max_pages,
        )
        if isinstance(dj, dict) and dj.get("error"):
            print("FAIL [discover]:", dj.get("error"), file=sys.stderr)
            return 1
        journeys = dj.get("journeys") or dj.get("data", {}).get("journeys") or []
        rows = []
        for j in journeys:
            if isinstance(j, dict):
                rows.append(
                    {
                        "name": j.get("journey_name") or j.get("name") or j.get("flow_name"),
                        "gating": j.get("include_in_release_gating"),
                        "why": (j.get("why_it_matters") or "")[:200],
                    }
                )
        _print_json({"stage": "discover", "journey_count": len(journeys), "journeys": rows})

    if "release" in stages:
        print("== release: run_release_check (replay) ==")
        rc = await run_release_check(
            app_url=app_url,
            profile_name=args.profile or None,
            mode="replay",
            headless=args.headless,
        )
        if isinstance(rc, dict) and rc.get("error"):
            details = (rc.get("blop_error") or {}).get("details") or {}
            if details.get("reason") == "no_flows_for_criticality":
                print(
                    "SKIP [release]: no recorded flows for this app_url and criticality filter. "
                    "Record journeys with record_test_flow, then re-run with --stages release."
                )
                return 0
            print("FAIL [release]:", rc.get("error"), file=sys.stderr)
            return 1
        run_id = rc.get("run_id") or (rc.get("data") or {}).get("run_id")
        release_id = rc.get("release_id") or (rc.get("data") or {}).get("release_id")
        if not run_id:
            print("FAIL [release]: missing run_id", file=sys.stderr)
            return 1
        print("release queued run_id=", run_id, "release_id=", release_id or "—")
        deadline = time.monotonic() + args.release_poll_secs
        status = None
        while time.monotonic() < deadline:
            tr = await get_test_results(run_id=run_id)
            status = tr.get("status")
            if status in GET_TEST_RESULTS_POLL_TERMINAL_STATUSES:
                break
            await asyncio.sleep(3.0)
        if status not in GET_TEST_RESULTS_POLL_TERMINAL_STATUSES:
            print("WARN [release]: poll timeout; check get_test_results later for", run_id)
            return 0
        _print_json(
            {
                "stage": "release",
                "run_id": run_id,
                "release_id": release_id,
                "status": status,
                "failed_cases": tr.get("failed_cases", [])[:5],
            }
        )

    print("OK: deeper_release_check finished")
    return 0


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument(
        "--stages",
        default="preflight,eval,discover",
        help="Comma-separated: preflight,eval,discover,release (default: preflight,eval,discover)",
    )
    p.add_argument(
        "--task",
        default="",
        help="Natural-language task for evaluate_web_task (else BLOP_DEEPER_CHECK_TASK or built-in default)",
    )
    p.add_argument("--profile", default="", help="Optional auth profile name for eval/discover/release")
    p.add_argument("--headless", action=argparse.BooleanOptionalAction, default=True)
    p.add_argument("--eval-max-steps", type=int, default=18)
    p.add_argument("--max-depth", type=int, default=2)
    p.add_argument("--max-pages", type=int, default=8)
    p.add_argument("--release-poll-secs", type=float, default=900.0)
    p.add_argument("--print-report", action="store_true", help="Print full markdown report after eval")
    args = p.parse_args()
    return asyncio.run(_run(args))


if __name__ == "__main__":
    raise SystemExit(main())
