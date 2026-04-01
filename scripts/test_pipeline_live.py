#!/usr/bin/env python3
"""Live smoke test for the pipeline engine using .env credentials.

Usage:
    python scripts/test_pipeline_live.py
    APP_URL=https://yourapp.com python scripts/test_pipeline_live.py

Requirements:
    - .env with GOOGLE_API_KEY (or BLOP_LLM_PROVIDER + matching key)
    - BLOP_APP_URL or APP_URL set to the target app (falls back to https://example.com for checks 1-3)

What it tests:
    1. ValidateStage + AuthStage directly (no browser)
    2. LLM_CALL_* event emission via contextvars
    3. ToolError diagnostic fields in finalize_tool_payload output
    4. evaluate_web_task live LLM + browser round-trip (only if BLOP_APP_URL is set)
"""

from __future__ import annotations

import asyncio
import os
from pathlib import Path

# Load .env before any blop imports
_env = Path(__file__).parent.parent / ".env"
if _env.exists():
    try:
        from dotenv import load_dotenv

        load_dotenv(_env, override=False)
    except ImportError:
        pass  # python-dotenv optional; rely on env already set


async def main() -> None:
    app_url = os.getenv("BLOP_APP_URL") or os.getenv("APP_URL") or "https://example.com"
    live_browser = bool(os.getenv("BLOP_APP_URL") or os.getenv("APP_URL"))

    provider = os.getenv("BLOP_LLM_PROVIDER", "google")
    print(f"[smoke] app_url      : {app_url}")
    print(f"[smoke] provider     : {provider}")
    print(f"[smoke] live_browser : {live_browser}")

    # ── Check 1: validate + auth pipeline (no browser) ──────────────────────
    print("\n[1/4] ValidateStage + AuthStage (no browser required)")
    from blop.engine.pipeline import RunContext, RunPipeline
    from blop.engine.stages.auth import AuthStage
    from blop.engine.stages.validate import ValidateStage

    class NullStage:
        async def run(self, ctx: RunContext) -> None:
            pass

    mini = RunPipeline(
        validate=ValidateStage(),
        auth=AuthStage(),
        execute=NullStage(),
        classify=NullStage(),
        report=NullStage(),
    )
    ctx = RunContext(run_id="smoke_01", app_url=app_url, flow_ids=[], profile_name=None)
    await mini.run(ctx)
    print(f"     validated_url : {ctx.validated_url}")
    print(f"     auth_state    : {ctx.auth_state}")
    print("     Events:")
    for ev in ctx.bus.events:
        print(f"       [{ev.stage}] {ev.event_type}: {ev.message}")
    assert ctx.validated_url is not None, "FAIL: validated_url is None after ValidateStage"
    print("     PASS")

    # ── Check 2: LLM event bus ───────────────────────────────────────────────
    print("\n[2/4] LLM_CALL_* event emission")
    from blop.engine.events import EventBus
    from blop.engine.llm_events import (
        emit_llm_fail,
        emit_llm_ok,
        emit_llm_start,
        llm_event_bus,
        set_llm_event_bus,
    )

    bus = EventBus("smoke_llm")
    token = set_llm_event_bus(bus)
    try:
        emit_llm_start(provider=provider, model="test-model", call_id="smoke_c1")
        emit_llm_ok(provider=provider, model="test-model", call_id="smoke_c1")
        emit_llm_fail(
            provider=provider,
            model="test-model",
            call_id="smoke_c2",
            error="simulated error",
        )
    finally:
        llm_event_bus.reset(token)

    llm_evts = [e.event_type for e in bus.events]
    assert "LLM_CALL_START" in llm_evts
    assert "LLM_CALL_OK" in llm_evts
    assert "LLM_CALL_FAIL" in llm_evts
    print(f"     events : {llm_evts}")
    print("     PASS")

    # ── Check 3: ToolError diagnostic fields ─────────────────────────────────
    print("\n[3/4] ToolError diagnostic fields")
    from blop.mcp.envelope import err_response, finalize_tool_payload

    resp = err_response(
        "BLOP_AUTH_PROFILE_NOT_FOUND",
        "Profile 'staging' not found",
        likely_cause="Profile was never created",
        suggested_fix="Run save_auth_profile with profile_name='staging'",
        retry_safe=False,
        stage="AUTH",
    )
    assert resp.error.likely_cause == "Profile was never created"
    assert resp.error.suggested_fix == "Run save_auth_profile with profile_name='staging'"

    raw = resp.model_dump()
    finalized = finalize_tool_payload(
        {"ok": False, "error": raw["error"]},
        request_id="smoke_req_01",
        tool_name="run_regression_test",
    )
    mcp_details = (finalized.get("mcp_error") or {}).get("details") or {}
    assert mcp_details.get("likely_cause") == "Profile was never created", f"likely_cause not propagated: {mcp_details}"
    print(f"     likely_cause  : {mcp_details['likely_cause']}")
    print(f"     suggested_fix : {mcp_details.get('suggested_fix')}")
    print("     PASS")

    # ── Check 4: evaluate_web_task live LLM + browser round-trip ─────────────
    if not live_browser:
        print("\n[4/4] Skipped — set BLOP_APP_URL to run live browser test")
    else:
        print(f"\n[4/4] evaluate_web_task live round-trip against {app_url}")
        from blop.tools.evaluate import evaluate_web_task

        result = await evaluate_web_task(
            app_url=app_url,
            task="Navigate to the homepage and verify the page title is not empty",
        )
        ok = result.get("ok", False)
        if ok:
            data = result.get("data") or {}
            decision = data.get("decision") or result.get("decision", "N/A")
            print(f"     ok={ok}  decision={decision}")
        else:
            mcp_err = result.get("mcp_error") or {}
            details = mcp_err.get("details") or {}
            print(f"     ok={ok}")
            print(f"     error         : {result.get('error')}")
            print(f"     likely_cause  : {details.get('likely_cause', 'N/A')}")
            print(f"     suggested_fix : {details.get('suggested_fix', 'N/A')}")
            print("     NOTE: live browser test failed (check error above)")
        print("     DONE")

    print("\n══════════════════════════════════════")
    print("  ALL SMOKE CHECKS COMPLETE")
    print("══════════════════════════════════════")


if __name__ == "__main__":
    asyncio.run(main())
