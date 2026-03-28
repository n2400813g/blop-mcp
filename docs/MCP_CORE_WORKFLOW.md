# Blop MCP core workflow

Internal reference for the **context-first, actions-second** agent loop. The MCP server exposes tools and `blop://` resources; prefer the context tools (`get_workspace_context`, `get_release_context`, …) for tight loops—small JSON, stable IDs.

## Backbone

```
load_context → select_journeys → plan → navigate & act → observe → record_evidence → summarize
```

| Step | Purpose | Concrete primitives (today) |
|------|---------|------------------------------|
| **load_context** | Preflight + workspace/release/journey pointers | `validate_release_setup`, `get_workspace_context`, `get_release_context`, `get_release_and_journeys`, `blop://journeys`, `blop://release/{id}/brief` |
| **select_journeys** | Choose what gates the release | `discover_critical_journeys`; filter by `include_in_release_gating`, `criticality_class`, staleness hints from `get_journeys_for_release` |
| **plan** | Map goals to steps | Agent reasoning; optional `IntentContract` / `RecordedFlow` |
| **navigate & act** | Drive the app | `run_release_check` (replay), `evaluate_web_task` (targeted smoke), or atomic tools: `navigate_to_url`, `navigate_to_journey`, `perform_step`, `get_page_snapshot`, `capture_artifact` |
| **observe** | Inspect outcomes | `get_test_results`, `blop://release/{id}/artifacts`, `blop://release/{id}/incidents` |
| **record_evidence** | Attach notes and artifacts | Regression artifacts under `runs/`; `record_run_observation` (idempotent per `run_id` + `observation_key`) |
| **summarize** | Ship / investigate / block | `ReleaseBrief` via `get_release_context` / `blop://release/{id}/brief`; `triage_release_blocker` |

## Default vs compat surface

- **`BLOP_ENABLE_COMPAT_TOOLS=false` (default):** Core release tools, auth, evaluation/recording/regression, **context read tools**, and **atomic browser tools** are visible. Legacy `browser_*` Playwright-compat names and `blop_v2_*` require `BLOP_ENABLE_COMPAT_TOOLS=true`.
- Prefer **`validate_release_setup`** over **`validate_setup`**, and **`discover_critical_journeys`** over **`discover_test_flows`**.

## Example: release-quality check

1. `validate_release_setup(app_url="https://app.example.com", profile_name="staging")`
2. `get_workspace_context()` → note `resource_uris` and `workspace_id`.
3. `get_release_and_journeys(release_id="rel-2025-03-26")` **or** `get_journeys_for_release(release_id=…)` after a brief exists.
4. `get_prd_and_acceptance_criteria(release_id=…)` / `get_ux_taxonomy()` as needed.
5. `run_release_check(app_url=…, flow_ids=[…], release_id=…)` → poll `get_test_results(run_id)`.
6. `record_run_observation(run_id, observation_key="ux_checkout", observation_payload={…})` for agent notes.
7. `triage_release_blocker(release_id=…)` or `triage_release_blocker(run_id=…)` on failures.

## Example: interactive exploration (atomic browser)

1. `validate_release_setup(app_url=…, profile_name=…)`
2. `navigate_to_journey(journey_id="<flow_id>")` or `navigate_to_url(…)`.
3. `get_page_snapshot()` → use `ref` / selectors from snapshot.
4. `perform_step({"action":"click","ref":"e3"})` / `type` / `wait` / `press_key`.
5. `capture_artifact(kind="screenshot", …)` → paths under `runs/{run_id}/` when `run_id` is supplied.

## Performance benchmarking

Timings and **live stdio MCP** (spawn `blop-mcp`, call `navigate_to_url` / `get_page_snapshot` like an agent): see [`docs/mcp_performance.md`](mcp_performance.md) (`scripts/benchmark_mcp_demoblaze.py` vs `scripts/mcp_stdio_e2e_demoblaze.py`).

## JSON schemas

Pydantic models for new tool envelopes and DTOs live under `src/blop/mcp/dto.py`. JSON Schema exports: `uv run python scripts/export_mcp_schemas.py` → `contracts/mcp/`.
