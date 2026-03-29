# Capability Placement For Open-Core BLOP

This memo makes the placement decision durable for the next three capability areas:

- semantic query/assertion layer,
- multi-agent smoke aggregator,
- journey-scoped API verification.

It is intentionally opinionated and optimized for BLOP's current **open-core SaaS** posture:

- keep **execution primitives** in `blop-mcp`,
- monetize **shared state, collaboration, governance, and cross-run intelligence** in hosted `blop`.

## Why this boundary exists

The current product framing and Linear roadmap point to a consistent split:

- **P1 OSS Core** is the durable local runtime: journey-aware execution, evidence capture, installability, and local-first operation.
- **P2 Hosted Workflow** is the system of record: workspace/project/release/run/case/artifact, dashboarding, release history, and sharing.
- **P3 Governance Engine** is where policy, signoff, and release gate enforcement belong.
- **P4 Intelligence Layer** is where aggregate insight, impact, and recommendation depth belong once signal quality is trusted.

The roadmap sequencing matters:

- **BLO-134** is still the active production-readiness bar for hosted P2.
- **BLO-129** customer context ingestion is explicitly deferred until after core hosted workflow stability.
- **BLO-60** preserves the requirement that local execution must still work when cloud services are unavailable.

The practical rule is:

**If a capability is needed to run validation locally, it belongs in OSS first. If it depends on shared state, customer context, policy, or aggregate history, it belongs in hosted.**

## Capability placement

| Capability | OSS `blop-mcp` | Hosted `blop` |
| --- | --- | --- |
| Semantic query/assertion layer | Query/assertion runtime, structured assertion types, replay/record/debug integration, evidence extraction for a single run | Shared assertion catalog, release-over-release comparison, workspace/project-level curation, context-aware assertion packs after BLO-129 |
| Multi-agent smoke aggregator | Bounded local preflight smoke mode, deterministic aggregation, non-authoritative findings that feed release validation | Scheduled and managed orchestration, shared templates, trend views, alerts, release-history comparisons, team-level visibility |
| Journey-scoped API verification | Journey-linked API expectations, request/response evidence, dependency checks scoped to one replay or debug session | Cross-run dependency views, aggregation by release/journey, policy/reporting integration, hosted release-page visibility |

## Packaging by tier

### OSS / local runtime

What stays in `blop-mcp`:

- semantic assertions as execution and evidence primitives,
- bounded smoke preflight on one local runtime,
- journey-scoped API verification tied to replay/debug output,
- no team dashboards,
- no shared libraries/catalogs,
- no policy enforcement,
- no cross-run learning.

### Hosted Team

What becomes paid hosted value:

- syncing and indexing these signals into the existing hosted release/run/case model,
- release-page and run-detail visibility,
- shared assertion catalog per workspace/project,
- smoke history and release-over-release diffs,
- API dependency evidence shown alongside release artifacts,
- notifications and collaboration hooks.

### Hosted Enterprise

What belongs in premium hosted layers:

- policy-aware gating and signoff,
- managed higher-concurrency smoke orchestration,
- assertion packs informed by customer Linear/PRD ingestion,
- cross-run insight and recommendation layers,
- dependency intelligence that spans releases and environments.

## What to build in OSS now

### 1. Semantic query/assertion layer

Build this in OSS as a **runtime primitive**, not as a standalone query-language product.

It should:

- extend structured assertions,
- power replay, recording, debug, and release-brief evidence,
- work without hosted dependencies,
- stay scoped to business-relevant facts needed for release confidence.

It should not:

- become a general scraping surface,
- or depend on customer context ingestion to work.

### 2. Multi-agent smoke aggregator

Build this in OSS as an **optional preflight mode**.

It should:

- remain bounded and deterministic,
- produce structured smoke findings,
- stay explicitly non-authoritative for final ship/no-ship decisions,
- complement recorded-journey replay rather than replace it.

It should not:

- redefine the canonical workflow,
- or require hosted orchestration to deliver local value.

### 3. Journey-scoped API verification

Build this in OSS as **journey-linked replay evidence**, not as a general API toolbox.

It should:

- explain browser failures with scoped network evidence,
- validate expected dependency behavior for critical journeys,
- flow into run results, release briefs, and blocker triage.

It should not:

- become a broad Postman-like product surface,
- or fragment BLOP into separate browser and API testing stories.

## Hosted follow-on work

Only after hosted P2 is stable should these capabilities be productized in hosted `blop`.

### Wave 2: post-BLO-134

- sync semantic/smoke/API-verification outputs into hosted run and release records,
- show them on release pages and run detail,
- add trend and comparison views,
- keep customer-context dependency out of the first hosted version.

### Wave 3: post-BLO-129

- connect customer Linear and PRD context to these capabilities,
- surface "what we tested against" using semantic assertions and smoke templates,
- let hosted workspaces curate assertion packs and release-specific expectations.

### Wave 4: P3/P4 premium layers

- policy enforcement and signoff,
- managed orchestration,
- cross-run recommendation systems,
- dependency and impact intelligence across releases and environments.

## Public interface guidance

For OSS, the preferred API posture is:

- extend existing structured assertion contracts rather than adding a brand-new standalone query product,
- add smoke-preflight as an optional mode under the release-confidence workflow,
- add API expectation/evidence fields to run and replay outputs,
- keep hosted-only concerns out of the OSS execution contract.

For hosted, the preferred API posture is:

- reuse the existing hosted release/run/case/artifact model from the P2 workspace model,
- avoid inventing hosted-only execution APIs as the first step,
- add indexing, comparison, and policy surfaces after sync and release pages are stable.

## Decision checklist

When placing future work on these capability areas, use this checklist:

1. Does it need to run when hosted services are unavailable?
2. Is it required to produce per-run release evidence locally?
3. Does it require shared workspace state or team collaboration?
4. Does it depend on customer roadmap/context ingestion?
5. Does it depend on policy, signoff, or aggregate historical analysis?

If answers are mostly `1-2`, build it in OSS first.

If answers are mostly `3-5`, build it in hosted.
