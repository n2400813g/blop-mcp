# AGENTS.md

This file provides guidance to Codex (Codex.ai/code) when working with code in this repository.

## Project Overview

**blop** is an MCP-native **release confidence control plane** for web applications. It combines Browser-Use execution (backed by Google Gemini, Anthropic, or OpenAI) with business-critical journey context, run evidence, and risk governance so teams can make reliable ship/no-ship decisions.

It exposes 26 MCP tools (14 core v1 + 12 v2 surface tools) and 13 MCP resources via a FastMCP server that integrates with Cursor and Codex.

### Product Thesis (internal shorthand)

- Teams do not have a "generate more tests" problem; they have a **release confidence** problem.
- Generic AI test runners optimize for action throughput; blop optimizes for **decision quality under uncertainty**.
- The moat is persistent context + evidence + risk scoring + remediation/correlation workflows, not one-shot browser automation.

## Setup & Installation

```bash
uv venv && source .venv/bin/activate
uv pip install -e .
playwright install chromium
```

Required environment variable: `GOOGLE_API_KEY` (Google Gemini access), or set `BLOP_LLM_PROVIDER=anthropic`/`openai` with the corresponding key.

See `.env.example` for all optional env vars.

## Dev: Test MCP Tools Interactively

```bash
blop-inspect   # opens MCP Inspector at http://localhost:5173
```

## Optional HTTP SSE server

```bash
pip install blop[server]
blop-http      # serves run health streams at http://localhost:8765
```

## Running the MCP Server

```bash
blop-mcp
# or
blop
# or
python -m blop.server
```

Cursor MCP config: copy `.cursor/mcp.json.example` to `.cursor/mcp.json`, set absolute paths and env (the real file is gitignored — never commit secrets).

Documentation contract and agent-facing product context live in `docs/DOC_CONTRACT.md` and `docs/AGENT_CONTEXT_PACK.md`. Set `BLOP_ENABLE_COMPAT_TOOLS=true` only when you need `browser_*` / `blop_v2_*` parity tools.

## Production baseline

- Primary production transport is local managed `stdio` (client launches `blop-mcp`).
- See `docs/production_setup.md` for hardening, runbook, and deployment templates.
- Use `deploy/prod.env.template` as the config contract (absolute paths, capability profile, URL safety).

## Architecture

All logic lives in `src/blop/`.

The system follows a **validate → contextualize → execute → decide** control-plane loop:

1. **`server.py`** — FastMCP entry point exposing 26 tools and 13 resources. Suppresses all logging on import.

2. **`config.py`** — All env var reading in one place. Also sets `ANONYMIZED_TELEMETRY=false` and `BROWSER_USE_LOGGING_LEVEL=CRITICAL`. Provides shared helpers `check_llm_api_key()` and `validate_app_url()`.

3. **`schemas.py`** — Pydantic v2 models for all tool I/O: `AuthProfile`, `FlowStep`, `RecordedFlow`, `FailureCase`, and output result types.

4. **`engine/browser.py`** — `make_browser_profile()` factory. Always disables user data dir, disables security, sets network idle timeouts.

5. **`engine/auth.py`** — `resolve_storage_state()` handles `env_login`, `storage_state`, `cookie_json`. Auth state cached 1h per profile. Supports `user_data_dir` (persistent Chromium context) for anti-bot OAuth. `validate_auth_session()` checks whether a storage_state session is still valid for an app URL.

6. **`engine/discovery.py`** — Inventory-first crawl engine with configurable breadth (`max_depth`, `max_pages`, seeds, include/exclude patterns), per-page compact ARIA structure capture, plus Gemini planning to produce 3–8 flow dicts with `{flow_name, goal, likely_assertions, business_criticality}`.

7. **`engine/recording.py`** — `record_flow()` runs a Browser-Use agent and captures each action as a `FlowStep` list.

8. **`engine/regression.py`** — `execute_flow()` replays a `RecordedFlow`, captures per-step screenshots, console/network errors. `run_flows()` runs in parallel (semaphore=5). Pass/fail via keyword matching.

9. **`engine/interaction.py`** — Resilient click/fill/drag helpers with CSS → text → vision fallback chain.

10. **`engine/vision.py`** — Gemini screenshot fallback: `find_element_coords()`, `click_by_vision()`, `assert_by_vision()`.

11. **`engine/classifier.py`** — `classify_case()` assigns severity via Gemini. `classify_run()` aggregates. `_generate_next_actions()` returns 3 concrete fixes.

12. **`storage/sqlite.py`** — aiosqlite. 15 tables including `auth_profiles`, `recorded_flows`, `runs`, `run_cases`, `artifacts`, `site_inventories`, `context_graphs`, `run_health_events`, `release_snapshots`, `incident_clusters`, `remediation_drafts`, `telemetry_signals`, `correlation_reports`, `schema_version`. `init_db()` creates/migrates on startup with versioned migrations.

13. **`storage/files.py`** — Path helpers for `runs/screenshots/`, `runs/traces/`, `runs/console/`, `runs/network/`.

14. **`reporting/results.py`** — `build_report()` aggregates run+cases into structured response.

15. **`engine/llm_factory.py`** — Multi-provider LLM factory: `make_planning_llm()`, `make_agent_llm()`, `make_message()` for Google/Anthropic/OpenAI.

16. **`engine/context_graph.py`** — App archetype detection, site context graph builder, and graph diff for v2 surface tools.

## Canonical Release Workflow

Use the release-confidence surface by default:

1. `validate_release_setup`
2. `discover_critical_journeys`
3. `record_test_flow`
4. `run_release_check`
5. `triage_release_blocker`

Treat legacy aliases like `validate_setup`, `discover_test_flows`, and `run_regression_test` as compatibility affordances, not the primary path.

## Core MCP Tools (Canonical + Compatibility)

| Tool | Purpose |
|------|---------|
| `validate_release_setup` | Canonical preflight for release gating: runtime, Chromium, DB, app reachability, and auth validity |
| `discover_critical_journeys` | Canonical discovery tool: plan business-ranked journeys and flag what should gate releases |
| `record_test_flow` | Run agent for a goal and capture steps (optional business_criticality) |
| `run_release_check` | Canonical release-confidence execution path: replay journeys and return ship/investigate/block output |
| `triage_release_blocker` | Summarize likely cause, evidence, impact, and next actions for a blocked release |
| `evaluate_web_task` | One-shot evaluator: URL + task → rich report with screenshots/console/network evidence |
| `setup_browser_state` | Interactive login capture (alias for capture_auth_session, web-eval-agent compatible) |
| `cancel_run` | Cancel a running/queued test and mark as cancelled |
| `explore_site_inventory` | Crawl-only inventory map (routes/forms/buttons/signals) without flow planning |
| `get_page_structure` | Snapshot one page's compact ARIA interactive layout (role/name pairs) |
| `save_auth_profile` | Persist auth config (env_login/storage_state/cookie_json, optional user_data_dir) |
| `capture_auth_session` | Headed browser: user logs in interactively; session saved and profile created |
| `get_test_results` | Retrieve run results and severity report |
| `get_run_health_stream` | Run health events (per-step progress, assertions, errors) |
| `get_risk_analytics` | Flaky step leaderboard and business risk breakdown |
| `list_runs` | List recent runs by status (running/completed/failed/etc.) |
| `list_recorded_tests` | Compatibility-oriented listing of saved flows; prefer `blop://journeys` for canonical planning context |
| `debug_test_case` | Re-run a case headed+verbose for evidence |
| `discover_test_flows` | Deprecated compatibility alias for journey discovery |
| `run_regression_test` | Deprecated compatibility alias for replay execution |
| `validate_setup` | Deprecated compatibility alias for preflight |

## V2 Surface Tools (12)

| Tool | Purpose |
|------|---------|
| `blop_v2_get_surface_contract` | Schema contract for all v2 tools (request/response shapes + examples) |
| `blop_v2_capture_context` | Crawl → build context graph → diff against previous |
| `blop_v2_compare_context` | Compare two context graph snapshots |
| `blop_v2_assess_release_risk` | Risk score combining graph diff, test coverage, incidents |
| `blop_v2_get_journey_health` | Per-flow pass rates within a time window |
| `blop_v2_cluster_incidents` | Cluster similar failures across runs |
| `blop_v2_generate_remediation` | LLM-generated fix hypotheses for an incident cluster |
| `blop_v2_ingest_telemetry_signals` | Store external signals (error rates, deploy events) |
| `blop_v2_get_correlation_report` | Correlate telemetry signals with incident clusters |
| `blop_v2_suggest_flows_for_diff` | Map changed files → affected flows via context graph |
| `blop_v2_autogenerate_flows` | Synthesize flow specs for unmatched graph intents |
| `blop_v2_archive_storage` | Archive old runs and telemetry data |

## Artifacts

Screenshots, traces, and console logs are in `runs/<type>/<run_id>/`. SQLite DB at `.blop/runs.db`.

## Key Implementation Notes

- All logging is suppressed on `config.py` import to prevent JSON-RPC interference.
- `make_browser_profile()` supports optional `user_data_dir` for persistent context; otherwise disables user data dir and browser security features.
- Default LLM: `gemini-2.5-flash` for agents and planning. Configurable via `BLOP_LLM_PROVIDER` (google/anthropic/openai) and `BLOP_LLM_MODEL`.
- `run_release_check` is the flagship release-confidence entry point. In replay mode it returns immediately with a `run_id`; callers then poll `get_test_results`. Compatibility alias `run_regression_test` maps onto the same underlying replay path.
- `business_criticality` (revenue, activation, retention, support, other) is stored on flows and cases; classifier and reporting use it for severity labels (e.g. "BLOCKER in revenue flow").
- Exploration profile defaults are configurable via `BLOP_EXPLORATION_PROFILE` (`default`/`saas_marketing`) with override knobs for network idle, SPA settle, agent retries, and crawl page limits.
