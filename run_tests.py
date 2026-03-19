"""
blop full-capacity test runner against https://sens-ai.dk
Runs phases 1-11 sequentially, prints structured output.
"""
import asyncio
import json
import sys
import time
from datetime import datetime

sys.path.insert(0, "src")

APP_URL = "https://www.sens-ai.dk"
AUTH_PROFILE = "sens_ai_test"
DIVIDER = "=" * 70


def section(title: str):
    print(f"\n{DIVIDER}")
    print(f"  {title}")
    print(DIVIDER)


def result(label: str, data: dict, keys: list = None):
    print(f"\n[{label}]")
    if "error" in data:
        print(f"  ERROR: {data['error']}")
        return
    if keys:
        for k in keys:
            v = data.get(k)
            if v is not None:
                print(f"  {k}: {v}")
    else:
        # Print top-level scalar values
        for k, v in data.items():
            if isinstance(v, (str, int, float, bool)) and k not in ("formatted_report",):
                print(f"  {k}: {v}")


# ─── Phase 0: Auth setup ─────────────────────────────────────────────────────

async def phase0_auth() -> bool:
    section("PHASE 0 — Auth setup: save_auth_profile + validate_setup")
    import os
    from blop.tools.auth import save_auth_profile
    from blop.tools.validate import validate_setup

    login_url = os.getenv("LOGIN_URL", "https://www.sens-ai.dk/auth")
    print(f"  profile_name:  {AUTH_PROFILE}")
    print("  auth_type:     env_login")
    print(f"  login_url:     {login_url}")
    print(f"  username_env:  TEST_USERNAME -> {os.getenv('TEST_USERNAME', '(not set)')}")
    print(f"  password_env:  TEST_PASSWORD -> {'(set)' if os.getenv('TEST_PASSWORD') else '(not set)'}")

    # Creates the profile and attempts to resolve storage state immediately.
    t0 = time.time()
    r = await save_auth_profile(
        profile_name=AUTH_PROFILE,
        auth_type="env_login",
        login_url=login_url,
        username_env="TEST_USERNAME",
        password_env="TEST_PASSWORD",
    )
    elapsed = round(time.time() - t0, 1)
    status = r.get("status", "?")
    print(f"\n  save_auth_profile -> status={status} ({elapsed}s)")
    if r.get("warning"):
        print(f"  WARNING: {r['warning']}")
    if r.get("error"):
        print(f"  ERROR: {r['error']}")
        print("  Auth setup failed - continuing without auth (public routes only)")
        return False

    # Belt-and-suspenders check: validate profile resolution and session state.
    t0 = time.time()
    vr = await validate_setup(app_url=APP_URL, profile_name=AUTH_PROFILE)
    elapsed = round(time.time() - t0, 1)
    v_status = vr.get("status", "?")
    print(f"  validate_setup -> status={v_status} ({elapsed}s)")
    auth_check = next((c for c in vr.get("checks", []) if c.get("name") == "auth_profile"), None)
    if auth_check:
        print(
            f"  auth_profile check: {'PASS' if auth_check.get('passed') else 'FAIL'} - "
            f"{auth_check.get('message', '')}"
        )
    for w in vr.get("warnings", []):
        print(f"  WARN: {w}")

    auth_valid = status in ("saved", "saved_with_warning") and v_status != "blocked"
    if auth_valid:
        print(f"\n  Auth profile '{AUTH_PROFILE}' ready.")
    else:
        print("\n  Auth not fully validated - phases will fall back to auto env-login.")
    return auth_valid


# ─── Phase 1: DB init + validate setup ──────────────────────────────────────

async def phase1_validate():
    section("PHASE 1 — Preflight: validate_setup")
    from blop.storage.sqlite import init_db
    await init_db()
    print("  DB initialised OK")

    from blop.tools.validate import validate_setup
    t0 = time.time()
    r = await validate_setup(app_url=APP_URL)
    elapsed = round(time.time() - t0, 1)
    result("validate_setup", r, ["status", "blockers", "warnings"])
    print(f"  checks: {r.get('checks', {})}")
    print(f"  elapsed: {elapsed}s")
    return r.get("status") != "blocked"


# ─── Phase 2: Site Intelligence ─────────────────────────────────────────────

async def phase2_inventory():
    section("PHASE 2a — explore_site_inventory (crawl-only)")
    from blop.tools.discover import explore_site_inventory
    t0 = time.time()
    r = await explore_site_inventory(
        app_url=APP_URL,
        profile_name=AUTH_PROFILE,
        max_depth=2,
        max_pages=15,
    )
    elapsed = round(time.time() - t0, 1)
    summary = r.get("inventory_summary", {})
    inv = r.get("inventory", {})
    print(f"\n  routes_found: {summary.get('routes_found')}")
    print(f"  crawled_pages: {summary.get('crawled_pages')}")
    print(f"  app_archetype: {summary.get('app_archetype')}")
    print(f"  auth_signals: {summary.get('auth_signals', [])}")
    print(f"  business_signals: {summary.get('business_signals', [])}")
    print(f"  structured_pages: {summary.get('structured_pages')}")
    if inv:
        print(f"  routes: {inv.get('routes', [])[:10]}")
        headings = inv.get('headings', [])
        print(f"  headings: {headings[:8]}")
        buttons = [b.get('text', '') for b in inv.get('buttons', [])[:8]]
        print(f"  buttons: {buttons}")
    print(f"  elapsed: {elapsed}s")
    return r


async def phase2_discover():
    section("PHASE 2b — discover_test_flows (BFS + Gemini planning)")
    from blop.tools.discover import discover_test_flows
    t0 = time.time()
    r = await discover_test_flows(
        app_url=APP_URL,
        profile_name=AUTH_PROFILE,
        business_goal=(
            "Find the 5 most revenue-critical user journeys: "
            "demo request, trial signup, pricing exploration, contact, and any onboarding. "
            "Include any authenticated dashboard or post-login flows."
        ),
        max_depth=2,
        max_pages=15,
        return_inventory=True,
    )
    elapsed = round(time.time() - t0, 1)
    print(f"\n  flow_count: {r.get('flow_count')}")
    print(f"  quality: {r.get('quality')}")
    cg = r.get("context_graph", {})
    print(f"  context_graph: graph_id={cg.get('graph_id', '')[:12]}... nodes={cg.get('node_count')} edges={cg.get('edge_count')} archetype={cg.get('archetype')}")
    print(f"  elapsed: {elapsed}s")
    print("\n  Flows discovered:")
    flows = r.get("flows", [])
    for i, f in enumerate(flows, 1):
        print(f"    {i}. [{f.get('business_criticality','?').upper()}] {f.get('flow_name')} — {f.get('goal','')[:80]}")
        print(f"       confidence={f.get('confidence','?')} severity={f.get('severity_if_broken','?')}")
    return r


async def phase2_page_structure():
    section("PHASE 2c — get_page_structure (ARIA tree snapshot)")
    from blop.tools.discover import get_page_structure
    t0 = time.time()
    r = await get_page_structure(app_url=APP_URL)
    elapsed = round(time.time() - t0, 1)
    nodes = r.get("interactive_nodes", [])
    print(f"\n  current_url: {r.get('current_url')}")
    print(f"  interactive_node_count: {r.get('interactive_node_count')}")
    print(f"  sample nodes:")
    for n in nodes[:10]:
        print(f"    role={n.get('role','?'):12} name={n.get('name','?')[:50]}")
    print(f"  elapsed: {elapsed}s")
    return r


# ─── Phase 3: Security headers ───────────────────────────────────────────────

async def phase3_security():
    section("PHASE 3 — security_scan_url (HTTP headers)")
    from blop.tools.security import security_scan_url
    t0 = time.time()
    r = await security_scan_url(app_url=APP_URL)
    elapsed = round(time.time() - t0, 1)
    print(f"\n  security_score: {r.get('security_score')}")
    print(f"  headers_present: {r.get('headers_present', [])}")
    print(f"  headers_missing: {r.get('headers_missing', [])}")
    recs = r.get("recommendations", [])
    if recs:
        print(f"  recommendations:")
        for rec in recs[:5]:
            print(f"    - {rec}")
    print(f"  elapsed: {elapsed}s")
    return r


# ─── Phase 4: One-shot evaluation ────────────────────────────────────────────

async def phase4_evaluate():
    section("PHASE 4 — evaluate_web_task (one-shot agent evaluation)")
    from blop.tools.evaluate import evaluate_web_task
    t0 = time.time()
    r = await evaluate_web_task(
        app_url=APP_URL,
        task=(
            "Explore the main navigation and identify the primary CTA (demo/trial/contact). "
            "Describe the main value proposition visible on the homepage. "
            "Check whether the CTA leads to a working page. "
            "Note any console errors, broken links, or UX friction."
        ),
        headless=True,
        max_steps=15,
        capture=["screenshots", "console", "network"],
        format="markdown",
        save_as_recorded_flow=True,
        flow_name="homepage_evaluation",
    )
    elapsed = round(time.time() - t0, 1)
    print(f"\n  pass_fail: {r.get('pass_fail')}")
    print(f"  elapsed_secs: {r.get('elapsed_secs')}s (runner: {elapsed}s)")
    print(f"  run_id: {r.get('run_id')}")
    print(f"  recorded_flow_id: {r.get('recorded_flow_id')}")
    print(f"  agent_steps: {len(r.get('agent_steps', []))}")
    ev = r.get("evidence", {})
    print(f"  screenshots: {len(ev.get('screenshots', []))}")
    print(f"  console_errors: {len(ev.get('console_errors', []))}")
    print(f"  network_failures: {len(ev.get('network_failures', []))}")
    summary = r.get("summary", [])
    print(f"\n  Summary:")
    for bullet in summary:
        print(f"    • {bullet}")
    if ev.get("console_errors"):
        print(f"\n  Console errors:")
        for e in ev["console_errors"][:5]:
            print(f"    ! {e[:120]}")
    if ev.get("network_failures"):
        print(f"\n  Network failures:")
        for nf in ev["network_failures"][:5]:
            print(f"    ! {nf.get('method','?')} {nf.get('url','?')[:80]} → {nf.get('status','?')}")
    print(f"\n  Agent steps:")
    for s in r.get("agent_steps", [])[:20]:
        print(f"    {s['step']:2}. {s['description'][:90]}")
    return r


# ─── Phase 5: Standalone assertions ─────────────────────────────────────────

async def phase5_assertions():
    section("PHASE 5 — Standalone assertions")
    from blop.tools.assertions import verify_text_visible, verify_element_visible, verify_visual_state

    checks = [
        ("verify_text_visible 'AI'",
         lambda: verify_text_visible(APP_URL, "AI")),
        ("verify_text_visible 'sens'",
         lambda: verify_text_visible(APP_URL, "sens")),
        ("verify_element_visible role=link 'Contact'",
         lambda: verify_element_visible(APP_URL, "link", "Contact")),
        ("verify_element_visible role=link 'Pricing'",
         lambda: verify_element_visible(APP_URL, "link", "Pricing")),
        ("verify_visual_state 'primary CTA button visible above fold'",
         lambda: verify_visual_state(APP_URL, "The page has a clear primary call-to-action button visible above the fold")),
    ]

    results_out = []
    for label, coro_fn in checks:
        t0 = time.time()
        r = await coro_fn()
        elapsed = round(time.time() - t0, 1)
        passed = r.get("passed", r.get("result", False))
        status = "PASS" if passed else "FAIL"
        print(f"  [{status}] {label} ({elapsed}s)")
        if "error" in r:
            print(f"         ERROR: {r['error']}")
        results_out.append((label, passed))
    return results_out


# ─── Phase 6: Record flows ───────────────────────────────────────────────────

async def phase6_record(discover_result: dict):
    section("PHASE 6 — record_test_flow (record 3 critical flows)")
    from blop.tools.record import record_test_flow

    flows_to_record = []

    # Pick top 2 discovered flows by business_criticality + confidence
    discovered = discover_result.get("flows", [])
    priority_order = ["revenue", "activation", "retention", "support", "other"]
    priority_map = {name: idx for idx, name in enumerate(priority_order)}
    sorted_flows = sorted(
        discovered,
        key=lambda f: (
            priority_map.get(f.get("business_criticality"), priority_map["other"]),
            -f.get("confidence", 0),
        )
    )
    for f in sorted_flows[:2]:
        flows_to_record.append({
            "flow_name": f["flow_name"],
            "goal": f["goal"],
            "business_criticality": f.get("business_criticality", "other"),
        })

    # Auth-gated flows - verify login and post-login settings navigation.
    flows_to_record.append({
        "flow_name": "dashboard_login_verify",
        "goal": (
            "Navigate to https://www.sens-ai.dk/auth and log in using the credentials "
            "from the TEST_USERNAME and TEST_PASSWORD environment variables. "
            "After login, verify the authenticated dashboard or home screen loads "
            "with user-specific content visible (e.g. greeting, profile menu, or dashboard widget). "
            "Assert there is no login redirect or error message on the resulting page."
        ),
        "business_criticality": "revenue",
    })
    flows_to_record.append({
        "flow_name": "profile_settings_navigation",
        "goal": (
            "After logging in to https://www.sens-ai.dk, locate and navigate to the user profile "
            "or account settings page (look for avatar, account menu, or settings link). "
            "Verify the settings page loads and shows at least one editable field or user detail. "
            "Assert no 403 or redirect-to-login occurs."
        ),
        "business_criticality": "retention",
    })

    # Always add a navigation smoke test
    flows_to_record.append({
        "flow_name": "navigation_smoke",
        "goal": (
            "Navigate to https://sens-ai.dk, verify the homepage loads with a visible heading, "
            "click through the main navigation items (e.g. Product, Pricing, About or similar), "
            "verify each page loads without a 404 error, then return to homepage."
        ),
        "business_criticality": "activation",
    })

    recorded_ids = []
    for spec in flows_to_record:
        print(f"\n  Recording: {spec['flow_name']} [{spec['business_criticality'].upper()}]")
        print(f"  Goal: {spec['goal'][:100]}...")
        t0 = time.time()
        r = await record_test_flow(
            app_url=APP_URL,
            profile_name=AUTH_PROFILE,
            flow_name=spec["flow_name"],
            goal=spec["goal"],
            business_criticality=spec["business_criticality"],
        )
        elapsed = round(time.time() - t0, 1)
        status = r.get("status", "?")
        fid = r.get("flow_id", "?")
        steps = r.get("step_count", "?")
        print(f"  → status={status} flow_id={fid} steps={steps} ({elapsed}s)")
        if "error" in r:
            print(f"  ERROR: {r['error']}")
        elif status == "recorded":
            recorded_ids.append(fid)

    print(f"\n  Recorded {len(recorded_ids)} flows: {recorded_ids}")
    return recorded_ids


# ─── Phase 7: Run regression ─────────────────────────────────────────────────

async def phase7_regression(flow_ids: list):
    section("PHASE 7 — run_regression_test (hybrid replay)")
    from blop.tools.regression import run_regression_test
    from blop.tools.results import get_test_results, get_run_health_stream

    if not flow_ids:
        print("  No flow_ids to run, skipping regression")
        return None, None

    t0 = time.time()
    r = await run_regression_test(
        app_url=APP_URL,
        flow_ids=flow_ids,
        profile_name=AUTH_PROFILE,
        headless=True,
        run_mode="hybrid",
    )
    run_id = r.get("run_id")
    print(f"\n  run_id: {run_id}")
    print(f"  initial status: {r.get('status')}")
    print(f"  flow_count: {r.get('flow_count')}")

    # Poll until complete (max 5 minutes)
    print("\n  Polling for completion...")
    for attempt in range(60):
        await asyncio.sleep(5)
        result_r = await get_test_results(run_id)
        status = result_r.get("status", "?")
        print(f"  [{attempt*5:3}s] status={status}", end="")
        if status in ("completed", "failed", "cancelled"):
            elapsed = round(time.time() - t0, 1)
            print(f" (total: {elapsed}s)")
            print(f"\n  Final status: {status}")
            cases = result_r.get("cases", [])
            print(f"  Cases ({len(cases)}):")
            for c in cases:
                print(f"    [{c.get('status','?').upper():7}] {c.get('flow_name','?')} "
                      f"severity={c.get('severity','?')} "
                      f"replay={c.get('replay_mode','?')} "
                      f"healing={c.get('healing_decision','?')}")
                if c.get("step_failure_index") is not None:
                    print(f"             failed at step {c['step_failure_index']}")
                if c.get("assertion_failures"):
                    print(f"             assertion failures: {c['assertion_failures'][:3]}")
                fps = c.get("stability_fingerprints", [])
                if fps:
                    avg_entropy = round(sum(f.get("selector_entropy", 0) for f in fps) / len(fps), 3)
                    avg_drift = round(sum(f.get("drift_score", 0) for f in fps) / len(fps), 3)
                    print(f"             avg_selector_entropy={avg_entropy} avg_drift={avg_drift}")
                perf = c.get("performance_metrics", [])
                if perf:
                    for p in perf[:2]:
                        print(f"             perf[{p.get('url','?')[:40]}]: LCP={p.get('largestContentfulPaint','?')}ms FCP={p.get('firstContentfulPaint','?')}ms")
            sev = result_r.get("severity_counts", {})
            print(f"\n  severity_counts: {sev}")
            print(f"  next_actions: {result_r.get('next_actions', [])[:3]}")
            return run_id, result_r
        else:
            print()  # newline after status
    print(f"\n  Timed out waiting for run {run_id}")
    return run_id, None


# ─── Phase 8: Analytics + export ─────────────────────────────────────────────

async def phase8_analytics(run_id: str):
    section("PHASE 8 — Analytics & Export")
    from blop.tools.results import get_risk_analytics, list_runs, get_run_health_stream

    # Health stream
    print("\n[get_run_health_stream]")
    r = await get_run_health_stream(run_id=run_id, limit=50)
    events = r.get("events", [])
    print(f"  event_count: {len(events)}")
    for ev in events:
        print(f"  [{ev.get('event_type','?'):20}] {json.dumps(ev.get('payload',{}))[:80]}")

    # Risk analytics
    print("\n[get_risk_analytics]")
    r = await get_risk_analytics(limit_runs=30)
    print(f"  flaky_steps: {r.get('flaky_steps', [])[:3]}")
    print(f"  business_risk: {r.get('business_risk', {})}")
    print(f"  total_runs_analyzed: {r.get('total_runs_analyzed')}")

    # List runs
    print("\n[list_runs]")
    r = await list_runs(limit=10)
    runs = r.get("runs", [])
    print(f"  recent runs ({len(runs)}):")
    for run in runs[:5]:
        print(f"    {run.get('run_id','?')[:12]}... status={run.get('status','?')} "
              f"mode={run.get('run_mode','?')} started={(run.get('started_at') or '?')[:19]}")

    # Export report
    print("\n[export_test_report → markdown]")
    from blop.reporting.export import export_test_report
    r = await export_test_report(run_id, "markdown")
    print(f"  format: {r.get('format')}")
    print(f"  path: {r.get('path')}")
    print(f"  case_count: {r.get('case_count')}")

    # List recorded tests
    print("\n[list_recorded_tests]")
    from blop.storage.sqlite import list_flows
    flows = await list_flows()
    print(f"  total recorded flows: {len(flows)}")
    for f in flows[:8]:
        print(f"    {f.get('flow_id','?')[:12]}... {f.get('flow_name','?')} @ {(f.get('created_at') or '?')[:19]}")


# ─── Phase 9: V2 intelligence ─────────────────────────────────────────────────

async def phase9_v2():
    section("PHASE 9 — V2 Intelligence Layer")
    from blop.tools.v2_surface import (
        capture_context, get_journey_health, cluster_incidents,
        assess_release_risk, autogenerate_flows,
    )

    print("\n[blop_v2_capture_context]")
    t0 = time.time()
    r = await capture_context(
        app_url=APP_URL,
        max_depth=2,
        max_pages=12,
        intent_focus=["revenue", "activation"],
    )
    elapsed = round(time.time() - t0, 1)
    print(f"  graph_id: {r.get('graph_id','?')[:12]}...")
    print(f"  node_count: {r.get('node_count')}")
    print(f"  edge_count: {r.get('edge_count')}")
    print(f"  archetype: {r.get('archetype')}")
    diff = r.get("diff", {})
    print(f"  diff: added_nodes={len(diff.get('added_nodes',[]))} removed_nodes={len(diff.get('removed_nodes',[]))} confidence_delta={diff.get('confidence_delta','?')}")
    print(f"  elapsed: {elapsed}s")

    print("\n[blop_v2_get_journey_health]")
    r = await get_journey_health(app_url=APP_URL, window="7d")
    journeys = r.get("journeys", [])
    print(f"  journeys_found: {len(journeys)}")
    for j in journeys[:5]:
        print(f"    {j.get('journey_name','?')} criticality={j.get('criticality','?')} "
              f"pass_rate={j.get('pass_rate','N/A')} trend={j.get('trend','?')}")

    print("\n[blop_v2_cluster_incidents]")
    r = await cluster_incidents(app_url=APP_URL, window="7d", min_cluster_size=1)
    clusters = r.get("clusters", [])
    print(f"  clusters_found: {len(clusters)}")
    for c in clusters[:3]:
        print(f"    [{c.get('severity','?').upper()}] {c.get('title','?')[:60]} "
              f"affected_flows={c.get('affected_flows','?')}")

    print("\n[blop_v2_assess_release_risk]")
    r = await assess_release_risk(app_url=APP_URL)
    print(f"  risk_score: {r.get('risk_score')}")
    print(f"  risk_level: {r.get('risk_level')}")
    print(f"  top_risks: {r.get('top_risks', [])[:2]}")
    print(f"  recommended_actions: {r.get('recommended_actions', [])[:2]}")

    print("\n[blop_v2_autogenerate_flows]")
    r = await autogenerate_flows(
        app_url=APP_URL,
        criticality_filter=["revenue", "activation"],
        record=False,
        limit=3,
    )
    synth = r.get("synthesized", [])
    print(f"  total_unmatched_intents: {r.get('total_unmatched_intents')}")
    print(f"  synthesized: {len(synth)}")
    for s in synth[:3]:
        print(f"    [{s.get('business_criticality','?').upper()}] {s.get('flow_name','?')} — {s.get('goal','')[:70]}")


# ─── Phase 10: Debug a failed case if any ────────────────────────────────────

async def phase10_debug(run_id: str, run_result: dict):
    section("PHASE 10 — debug_test_case (if any failures)")
    if not run_result:
        print("  No run result available, skipping")
        return

    cases = run_result.get("cases", [])
    failed = [c for c in cases if c.get("status") in ("fail", "error")]
    if not failed:
        print("  All cases passed — no debug needed")
        return

    case = failed[0]
    case_id = case.get("case_id")
    print(f"\n  Debugging case: {case_id} [{case.get('flow_name')}]")

    from blop.tools.debug import debug_test_case
    t0 = time.time()
    r = await debug_test_case(run_id, case_id)
    elapsed = round(time.time() - t0, 1)
    print(f"  status: {r.get('status')}")
    print(f"  step_failure_index: {r.get('step_failure_index')}")
    print(f"  why_failed: {r.get('why_failed','?')[:200]}")
    print(f"  repro_steps: {r.get('repro_steps', [])[:3]}")
    print(f"  elapsed: {elapsed}s")


# ─── Phase 11: Code export ────────────────────────────────────────────────────

async def phase11_codegen(flow_ids: list):
    section("PHASE 11 — export_flow_as_code (Playwright test generation)")
    if not flow_ids:
        print("  No flows to export")
        return

    from blop.engine.codegen import export_flow_as_code
    r = await export_flow_as_code(flow_ids[0], language="python")
    print(f"\n  flow_id: {r.get('flow_id')}")
    print(f"  language: {r.get('language')}")
    print(f"  step_count: {r.get('step_count')}")
    print(f"  path: {r.get('path')}")
    if r.get("path"):
        import os
        if os.path.exists(r["path"]):
            with open(r["path"]) as f:
                code = f.read()
            print(f"\n  Generated code ({len(code)} chars):")
            for line in code.splitlines()[:40]:
                print(f"    {line}")


# ─── Main ────────────────────────────────────────────────────────────────────

async def main():
    print(f"\n{'#' * 70}")
    print(f"  blop MCP server — Full Capacity Test")
    print(f"  Target: {APP_URL}")
    print(f"  Started: {datetime.now().isoformat()[:19]}")
    print(f"{'#' * 70}")

    overall_start = time.time()
    errors = []

    # Phase 0 - Auth (must precede discovery, recording, regression)
    auth_ready = False
    try:
        auth_ready = await phase0_auth()
        if not auth_ready:
            print("\n  WARN: Auth not validated - auth-gated routes may be missed.")
    except Exception as e:
        errors.append(f"phase0_auth: {e}")
        print(f"  EXCEPTION in phase 0 (auth): {e}")
        print("  Continuing without auth profile.")

    # Phase 1
    try:
        ok = await phase1_validate()
        if not ok:
            print("\n  BLOCKED: validate_setup reports blockers. Continuing anyway...")
    except Exception as e:
        errors.append(f"phase1: {e}")
        print(f"  EXCEPTION in phase 1: {e}")

    # Phase 2
    inventory_result = {}
    discover_result = {}
    try:
        inventory_result = await phase2_inventory()
    except Exception as e:
        errors.append(f"phase2_inventory: {e}")
        print(f"  EXCEPTION: {e}")

    try:
        discover_result = await phase2_discover()
    except Exception as e:
        errors.append(f"phase2_discover: {e}")
        print(f"  EXCEPTION: {e}")

    try:
        await phase2_page_structure()
    except Exception as e:
        errors.append(f"phase2_page_structure: {e}")
        print(f"  EXCEPTION: {e}")

    # Phase 3
    try:
        await phase3_security()
    except Exception as e:
        errors.append(f"phase3: {e}")
        print(f"  EXCEPTION: {e}")

    # Phase 4
    eval_result = {}
    try:
        eval_result = await phase4_evaluate()
    except Exception as e:
        errors.append(f"phase4: {e}")
        print(f"  EXCEPTION: {e}")

    # Phase 5
    try:
        await phase5_assertions()
    except Exception as e:
        errors.append(f"phase5: {e}")
        print(f"  EXCEPTION: {e}")

    # Phase 6
    recorded_ids = []
    try:
        recorded_ids = await phase6_record(discover_result)
    except Exception as e:
        errors.append(f"phase6: {e}")
        print(f"  EXCEPTION: {e}")

    # Phase 7
    run_id = None
    run_result = None
    try:
        run_id, run_result = await phase7_regression(recorded_ids)
    except Exception as e:
        errors.append(f"phase7: {e}")
        print(f"  EXCEPTION: {e}")

    # Phase 8
    if run_id:
        try:
            await phase8_analytics(run_id)
        except Exception as e:
            errors.append(f"phase8: {e}")
            print(f"  EXCEPTION: {e}")

    # Phase 9
    try:
        await phase9_v2()
    except Exception as e:
        errors.append(f"phase9: {e}")
        print(f"  EXCEPTION: {e}")

    # Phase 10
    if run_id and run_result:
        try:
            await phase10_debug(run_id, run_result)
        except Exception as e:
            errors.append(f"phase10: {e}")
            print(f"  EXCEPTION: {e}")

    # Phase 11
    try:
        await phase11_codegen(recorded_ids)
    except Exception as e:
        errors.append(f"phase11: {e}")
        print(f"  EXCEPTION: {e}")

    # Summary
    section("FINAL SUMMARY")
    total = round(time.time() - overall_start, 1)
    print(f"\n  Total elapsed: {total}s")
    print(f"  Errors: {len(errors)}")
    for e in errors:
        print(f"    - {e}")
    if not errors:
        print("  All phases completed without exceptions.")


if __name__ == "__main__":
    asyncio.run(main())
