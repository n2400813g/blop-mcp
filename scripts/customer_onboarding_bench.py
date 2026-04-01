#!/usr/bin/env python3
"""Customer onboarding benchmark for blop MCP.

Simulates a net-new customer running blop against their SaaS app for the first time.
Measures every step against SaaS activation benchmarks.

Usage:
    uv run python scripts/customer_onboarding_bench.py
    APP_BASE_URL=https://yourapp.com uv run python scripts/customer_onboarding_bench.py
"""

from __future__ import annotations

import asyncio
import contextlib
import os
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

# ── path bootstrap ────────────────────────────────────────────────────────────
_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT / "src") not in sys.path:
    sys.path.insert(0, str(_ROOT / "src"))

# ── load .env before any blop imports ────────────────────────────────────────
_env_file = _ROOT / ".env"
if _env_file.exists():
    try:
        from dotenv import load_dotenv

        load_dotenv(_env_file, override=False)
    except ImportError:
        pass  # rely on shell env

# ── benchmark targets (seconds) ───────────────────────────────────────────────
TARGETS: dict[str, float] = {
    "preflight": 10.0,
    "auth_setup": 5.0,
    "evaluate": 90.0,
    "discover": 180.0,
    "record_flow_1": 300.0,
    "record_flow_2": 300.0,
    "regression": 600.0,
    "results": 5.0,
}

TTFV_TARGET_SECS = 120.0  # time-to-first-value: steps 1-3
ONBOARDING_TARGET_SECS = 1200.0  # 20 min total


@dataclass
class StepResult:
    name: str
    ok: bool
    elapsed: float
    target: float
    data: dict[str, Any] = field(default_factory=dict)
    error: str = ""


_results: list[StepResult] = []


@contextlib.contextmanager
def timed_step(name: str):
    """Context manager: records elapsed time and pass/fail for a step."""
    target = TARGETS.get(name, 999.0)
    start = time.monotonic()
    result = StepResult(name=name, ok=False, elapsed=0.0, target=target)
    _results.append(result)
    print(f"\n── {name} ──────────────────────────────────────")
    try:
        yield result
        if not result.error:  # allow explicit r.ok = False before early return
            result.ok = True
    except Exception as exc:
        result.ok = False
        result.error = str(exc)
        print(f"  ERROR: {exc}")
    finally:
        result.elapsed = time.monotonic() - start
        status = "PASS" if result.ok else "FAIL"
        within = "✓" if result.elapsed <= target else "✗ SLOW"
        print(f"  {status}  {result.elapsed:.1f}s  (target < {target:.0f}s)  {within}")


def _select_flows_to_record(journeys: list[dict]) -> list[dict]:
    """Pick top 2 flows: prefer include_in_release_gating=True, then by index."""
    gating = [j for j in journeys if j.get("include_in_release_gating")]
    non_gating = [j for j in journeys if not j.get("include_in_release_gating")]
    ordered = gating + non_gating
    return ordered[:2]


def _print_report(app_url: str) -> None:
    print("\n" + "═" * 60)
    print("  blop MCP — Customer Onboarding Benchmark")
    print(f"  Target : {app_url}")
    print("═" * 60)

    for i, r in enumerate(_results, 1):
        status = "PASS" if r.ok else "FAIL"
        within = "✓" if r.elapsed <= r.target else "✗"
        print(f"  Step {i:<2} {r.name:<20} {status}  {r.elapsed:>7.1f}s  (< {r.target:.0f}s) {within}")
        if not r.ok and r.error:
            print(f"         error: {r.error[:120]}")

    # ── computed metrics ──
    steps_1_3 = [r for r in _results if r.name in ("preflight", "auth_setup", "evaluate")]
    ttfv = sum(r.elapsed for r in steps_1_3)
    total = sum(r.elapsed for r in _results)
    failures_with_diagnosis = sum(
        1
        for r in _results
        if not r.ok
        and (r.data.get("likely_cause") or (r.data.get("mcp_error") or {}).get("details", {}).get("likely_cause"))
    )
    total_failures = sum(1 for r in _results if not r.ok)

    discovery_step = next((r for r in _results if r.name == "discover"), None)
    journey_count = len(discovery_step.data.get("journeys", [])) if discovery_step else 0

    results_step = next((r for r in _results if r.name == "results"), None)
    decision = (
        (results_step.data.get("decision") or results_step.data.get("release_recommendation") or "N/A")
        if results_step
        else "N/A"
    )

    diag_pct = int(failures_with_diagnosis / total_failures * 100) if total_failures else 100

    print("\n── Summary " + "─" * 49)
    ttfv_ok = "✓" if ttfv <= TTFV_TARGET_SECS else "✗"
    total_ok = "✓" if total <= ONBOARDING_TARGET_SECS else "✗"
    print(f"  TTFV (steps 1-3):       {ttfv:>6.1f}s   target < {TTFV_TARGET_SECS:.0f}s   {ttfv_ok}")
    print(f"  Full onboarding:        {total / 60:>6.1f}min target < {ONBOARDING_TARGET_SECS / 60:.0f}min  {total_ok}")
    print(f"  Decision:               {decision}")
    print(f"  Journeys discovered:    {journey_count}")
    print(f"  Error diagnosis:        {diag_pct}%  (likely_cause present on failures)")
    print("═" * 60)


async def main() -> int:
    app_url = (os.environ.get("APP_BASE_URL") or os.environ.get("BLOP_APP_URL") or "").strip().rstrip("/")
    if not app_url:
        print("FAIL: set APP_BASE_URL in .env or env", file=sys.stderr)
        return 2

    login_url = os.environ.get("LOGIN_URL") or f"{app_url}/auth"
    profile_name = "sens_ai_bench"  # noqa: F841 – used in subsequent tasks

    print("blop MCP — Customer Onboarding Benchmark")
    print(f"Target: {app_url}")
    print(f"Login:  {login_url}")

    # Steps 1-7 go here (added in subsequent tasks)

    _print_report(app_url)
    failed = sum(1 for r in _results if not r.ok)
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
