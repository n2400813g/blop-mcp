# Customer onboarding — release confidence with blop

This guide is for **teams** adopting blop as their MCP-native release-confidence layer (local `stdio` baseline). Adjust roles to your org size.

## Roles

| Role | Responsibility |
|------|----------------|
| **Platform / DevEx** | Install runtime, env files, MCP client config, capability profiles, secret injection |
| **QA or feature owner** | Chooses **release-gating** journeys (`business_criticality`: revenue / activation) |
| **Engineer on call** | Uses operator docs when runs fail (`docs/operator_failures.md`, `docs/SUPPORT_RUNBOOK.md`) |

## Day 0 — install (15–30 minutes)

1. Python 3.11+, `uv` or `pip`, `playwright install chromium`.
2. Install package: `uv pip install blop-mcp` (or equivalent).
3. Set **one** LLM provider key (`GOOGLE_API_KEY` or Anthropic/OpenAI per docs).
4. Copy `deploy/prod.env.template` → your env file; set **absolute** paths for `BLOP_DB_PATH`, `BLOP_RUNS_DIR`, `BLOP_DEBUG_LOG`.
5. Configure MCP client (Cursor / Claude Code) to launch `blop-mcp` with that env.
6. Run **`validate_release_setup`** once with your staging `app_url` and optional `profile_name`.

See also: `docs/production_setup.md`, client quickstarts in `docs/quickstart_*.md`.

## Day 1 — golden path (team workflow)

1. **`discover_critical_journeys`** on staging — review `include_in_release_gating`.
2. **`record_test_flow`** for each gating journey (prioritize revenue / activation).
3. **`run_release_check`** (`mode="replay"`) before merge or deploy.
4. Read **`get_test_results`** or `blop://release/{release_id}/brief` for **SHIP / INVESTIGATE / BLOCK**.
5. On blockers: **`triage_release_blocker`**, then fix product or refresh recordings per `docs/operator_failures.md`.

## What we support vs best-effort

| Supported baseline | Best-effort / environmental |
|--------------------|----------------------------|
| Local `stdio` MCP + SQLite + artifacts on a properly configured host | Flaky networks, third-party OAuth UI changes, LLM quota outages |
| Documented env contract and capability profiles | Exotic MCP transports not listed in `production_setup.md` |
| Schema migrations on package upgrade | Manual edits to SQLite or partial file deletes |

## Hosted sync (optional)

If `BLOP_PROJECT_ID` / hosted URL / token are set, the runtime may **push run summaries** to a hosted blop stack. That path is **optional**; local-only teams can leave those unset. Data crossing the boundary should be reviewed against `docs/DATA_HANDLING.md` and your vendor DPA.

## Upgrades

1. Read **`CHANGELOG.md`** for breaking changes.
2. Upgrade package in the same venv the MCP client uses.
3. Run **`validate_release_setup`** and a short **`run_release_check`** smoke on staging.

See **`docs/VERSIONING.md`** for semver rules.
