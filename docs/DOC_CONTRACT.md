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
- `AGENTS.md`
  - Repo-local implementation guidance for coding agents working in this codebase.

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
