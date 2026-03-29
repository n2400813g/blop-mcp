# Documentation Contract

This document is the repo-level bridge between the product thesis in Linear and the documentation in this repository.

Canonical product thesis in Linear:
- [Product thesis: analyst vision (P1–P4 map)](https://linear.app/blopaidk/document/product-thesis-analyst-vision-p1-p4-map-b747d50a40ae)

## What each source is responsible for

- `README.md`
  - Canonical public narrative for what blop is, who it helps, and the primary release-confidence workflow.
- `docs/production_setup.md`
  - Canonical operator setup and hardening guide for local managed `stdio`.
- `docs/quickstart_*.md`
  - Canonical client-specific setup and first-run guidance.
- `docs/AGENT_CONTEXT_PACK.md`
  - Canonical contributor and agent orientation for the current product framing and primary tool surface.
- `docs/oss_core_competitive_analysis.md`
  - Opinionated OSS-core competitive memo that maps adjacent open-source projects into adopt/adapt/avoid module recommendations for this repo.
- `docs/capability_placement_open_core.md`
  - Source of truth for where new capability layers should live across OSS core vs hosted blop under the current open-core roadmap.
- `AGENTS.md`
  - Repo-local implementation guidance for coding agents working in this codebase.
- `CHANGELOG.md`
  - User-visible release notes; paired with `docs/VERSIONING.md` for semver expectations.
- `SECURITY.md`
  - Vulnerability reporting and supported-version expectations.
- `docs/VERSIONING.md`
  - Semantic versioning and what counts as the public MCP/API contract.
- `docs/DATA_HANDLING.md`
  - Local data categories, sensitivity, and retention guidance for security reviews.
- `docs/CUSTOMER_ONBOARDING.md`
  - Team-oriented adoption path and supported vs best-effort baseline.
- `docs/SUPPORT_RUNBOOK.md`
  - What to collect when filing issues or escalating incidents.

## Product framing to preserve

blop should be described as:
- Release-centric: releases, journeys, runs, artifacts, and decisions are first-class.
- Evidence-backed: claims should link to screenshots, traces, logs, and structured results.
- Roadmap-aware: what gets tested and how results are interpreted should be informed by product context such as customer goals, acceptance criteria, and release scope.

## Program mapping

- P1 OSS Core
  - This repo primarily represents the local MCP runtime, journey discovery/recording/replay, evidence capture, and installability story.
- P2 Hosted Workflow
  - The broader product includes sync, release dashboard, history, sharing, and the hosted system of record where release confidence lives for teams.
- P3 Governance Engine
  - Policy, ship/hold/block reasoning, ownership, and signoff extend the release-confidence story beyond execution.
- P4 Intelligence Layer
  - Taxonomy, impact modeling, recurring insight, and telemetry correlation deepen the analyst layer once reliable aggregates exist.

## Repo truth vs product truth

- Do not present the OSS runtime as the whole product.
- Do present the OSS runtime as the P1 foundation and local execution plane that feeds the broader hosted workflow.
- Do not describe the hosted workflow as incidental or optional to the product thesis, even when local-first setup remains the operational baseline in this repo.

## Naming contract

Canonical release-confidence surface:
- `validate_release_setup`
- `discover_critical_journeys`
- `record_test_flow`
- `run_release_check`
- `triage_release_blocker`
- `blop://health`
- `blop://journeys`

Legacy aliases may still exist behind gating or deprecation paths, but repo docs should describe them as compatibility affordances rather than the default workflow.

## MCP tool and resource error envelope

Most tool and resource handlers that signal failure return a **flat** payload (plus any tool-specific keys):

- **`error`** (string): Human-readable message; kept for backward compatibility.
- **`blop_error`** (object): Structured machine-readable error with:
  - **`code`**: Stable identifier, typically `BLOP_<DOMAIN>_<DETAIL>` (e.g. `BLOP_RUN_NOT_FOUND`, `BLOP_VALIDATION_FAILED`).
  - **`message`**: Same text as top-level `error` in the common case.
  - **`retryable`**: Boolean hint for clients (e.g. storage quota vs validation).
  - **`details`**: Optional dict with field names, ids, `cause` (exception type), etc.

Handlers built with `blop.engine.errors.tool_error()` follow this shape. Some nested payloads (e.g. `get_process_insights` → `pm4py`) may attach a **`blop_error`** object inside a sub-key while the overall tool result remains successful. Assertion helpers (`verify_*`) still return **`passed: false`** with a string **`error`** per check — that is a result row, not necessarily the global tool envelope.

Canonical definitions live in `src/blop/engine/errors.py`.
