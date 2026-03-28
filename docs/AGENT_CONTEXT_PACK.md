# Agent Context Pack

This is the concise orientation pack for contributors and coding agents working in `blop-mcp`.

## Product thesis

blop is not "AI that clicks around a page." It is a release-confidence control plane and an opinionated product and quality analyst for web releases.

The three pillars to keep visible in docs and implementation are:
- Release-centric: new release -> risk assessment -> recommendation -> follow-up validation.
- Evidence-backed: screenshots, traces, logs, and structured case results support every decision.
- Roadmap-aware: customer goals, acceptance criteria, and release scope should shape what is gated and how failures are interpreted.

## What this repo represents

This repo is primarily the P1 OSS Core:
- local MCP runtime,
- journey discovery and recording,
- replay execution,
- evidence capture,
- installability and operator docs.

It also exposes integration points into the broader product thesis:
- sync and hosted workflow hooks,
- release resources and briefs,
- governance and intelligence-oriented surfaces.

## Canonical workflow

Default release-confidence path:
1. `validate_release_setup(app_url=...)`
2. `discover_critical_journeys(app_url=...)`
3. Review `blop://journeys`
4. `record_test_flow(...)` for missing or stale gated journeys
5. `run_release_check(..., mode="replay")`
6. `get_test_results(run_id=...)`
7. Read `blop://release/{release_id}/brief`
8. `triage_release_blocker(...)` when the decision is not `SHIP`

## Surface names to prefer

Prefer these names in docs, prompts, and examples:
- `validate_release_setup`
- `discover_critical_journeys`
- `run_release_check`
- `triage_release_blocker`
- `blop://health`
- `blop://journeys`

## Legacy surface guidance

These names are legacy or compatibility-oriented and should not be presented as the default path:
- `validate_setup`
- `discover_test_flows`
- `run_regression_test`
- `list_recorded_tests`

If mentioned, label them clearly as deprecated aliases, compatibility tools, or lower-level primitives relative to the canonical release-confidence workflow.

## Contributor doc style

- Keep the product narrative aligned with the Linear thesis.
- Distinguish clearly between the OSS local runtime and the hosted workflow.
- Keep operator docs operationally local-first.
- Avoid reducing blop to a generic browser runner; the decision and evidence layer is the point.
