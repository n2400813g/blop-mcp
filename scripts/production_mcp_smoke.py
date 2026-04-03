#!/usr/bin/env python3
"""In-process smoke: health → workspace → discover → release check → release context → resources.

Requires repo-root `.env` with APP_BASE_URL (or BLOP_APP_URL), LLM key, and Chromium for replay.
Exit non-zero on first failed assertion (prints step name + error).
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
import time
from pathlib import Path

# Repo root on path
_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT / "src"))


async def main() -> int:
    import blop.config  # noqa: F401 — loads .env
    from blop.config import GET_TEST_RESULTS_POLL_TERMINAL_STATUSES
    from blop.server import health_resource
    from blop.tools.context_read import get_release_context, get_workspace_context
    from blop.tools.journeys import discover_critical_journeys
    from blop.tools.release_check import run_release_check
    from blop.tools.results import get_artifact_index_resource, get_test_results

    # Match flow rows stored without a trailing slash (common .env drift: https://app/ vs https://app).
    app_url = (os.environ.get("APP_BASE_URL") or os.environ.get("BLOP_APP_URL") or "").strip().rstrip("/")
    if not app_url:
        print("FAIL: set APP_BASE_URL or BLOP_APP_URL", file=sys.stderr)
        return 2

    step = "health_resource"
    try:
        h = await health_resource()
        assert isinstance(h, dict) and h.get("db_reachable") is not False, f"bad health: {h}"
    except Exception as e:
        print(f"FAIL [{step}]: {e}", file=sys.stderr)
        return 1

    step = "get_workspace_context"
    try:
        ctx = await get_workspace_context(use_cache=False)
        assert ctx.get("ok") is True, ctx
    except Exception as e:
        print(f"FAIL [{step}]: {e}", file=sys.stderr)
        return 1

    step = "discover_critical_journeys"
    try:
        dj = await discover_critical_journeys(app_url=app_url, max_depth=1, max_pages=2)
        assert "error" not in dj, dj
        journeys = dj.get("journeys") or dj.get("data", {}).get("journeys") or []
        assert len(journeys) >= 1, "expected at least one journey"
    except Exception as e:
        print(f"FAIL [{step}]: {e}", file=sys.stderr)
        return 1

    step = "run_release_check"
    try:
        rc = await run_release_check(app_url=app_url, mode="replay", headless=True)
        assert "error" not in rc, rc
        run_id = rc.get("run_id") or (rc.get("data") or {}).get("run_id")
        release_id = rc.get("release_id") or (rc.get("data") or {}).get("release_id")
        assert run_id, f"missing run_id in {rc}"
    except Exception as e:
        print(f"FAIL [{step}]: {e}", file=sys.stderr)
        return 1

    step = "get_test_results_poll"
    try:
        deadline = time.monotonic() + 900.0
        status = None
        while time.monotonic() < deadline:
            tr = await get_test_results(run_id=run_id)
            status = tr.get("status")
            if status in GET_TEST_RESULTS_POLL_TERMINAL_STATUSES:
                break
            await asyncio.sleep(3.0)
        assert status in GET_TEST_RESULTS_POLL_TERMINAL_STATUSES, f"timeout polling run {run_id}"
    except Exception as e:
        print(f"FAIL [{step}]: {e}", file=sys.stderr)
        return 1

    step = "get_release_context"
    try:
        assert release_id, "release_id missing from run_release_check response"
        gr = await get_release_context(release_id=release_id, use_cache=False)
        assert gr.get("ok") is True, gr
        data = gr.get("data") or {}
        decision = str(data.get("decision") or "").upper()
        assert decision in {"SHIP", "INVESTIGATE", "BLOCK"}, f"decision={decision!r} full={json.dumps(gr)[:500]}"
    except Exception as e:
        print(f"FAIL [{step}]: {e}", file=sys.stderr)
        return 1

    step = "blop_release_brief_resource"
    try:
        from blop.tools import resources as res

        brief = await res.release_brief_resource(release_id)
        assert isinstance(brief, dict), brief
        assert not brief.get("error"), brief
        assert brief.get("decision") or brief.get("release_id"), f"empty brief: {brief}"
    except Exception as e:
        print(f"FAIL [{step}]: {e}", file=sys.stderr)
        return 1

    step = "run_artifacts_resource"
    try:
        art = await get_artifact_index_resource(run_id)
        assert isinstance(art, dict), art
        assert art.get("run_id") == run_id or "cases" in art or "artifacts" in art, art
    except Exception as e:
        print(f"FAIL [{step}]: {e}", file=sys.stderr)
        return 1

    print("OK: production_mcp_smoke completed all steps")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
