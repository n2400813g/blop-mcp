# BLOP OSS-Core Competitive Analysis

This memo turns the current OSS landscape into an opinionated build-vs-copy recommendation for `blop-mcp`.

It is intentionally scoped to the **OSS core** in this repo:
- local MCP runtime,
- journey discovery and recording,
- replay execution,
- evidence capture,
- release-confidence reporting.

It is not a generic survey of browser agents. The question here is narrower:

**Which behaviors and modules should BLOP replicate from adjacent OSS projects without diluting the release-confidence control-plane thesis?**

## Recommendation posture

- Replicate **behavior**, not broad tool surface area.
- Prefer **accessibility-first evidence and deterministic contracts** over agent cleverness.
- Keep `validate_release_setup -> discover_critical_journeys -> record_test_flow -> run_release_check -> triage_release_blocker` as the canonical workflow.
- Reject changes that push BLOP toward a generic browser runner, API toolbox, or multi-backend orchestration shell.

## BLOP baseline today

BLOP already overlaps materially with the strongest OSS references:

- Accessibility-first page state already exists in [`src/blop/engine/dom_context.py`](../src/blop/engine/dom_context.py), [`src/blop/engine/page_state.py`](../src/blop/engine/page_state.py), [`src/blop/engine/snapshots.py`](../src/blop/engine/snapshots.py), and [`src/blop/engine/snapshot_refs.py`](../src/blop/engine/snapshot_refs.py).
- Discovery already supports bounded, section-aware parallel crawl behavior in [`src/blop/engine/discovery.py`](../src/blop/engine/discovery.py).
- Replay already captures evidence, bounded concurrency, healing constraints, drift summaries, and run-health events in [`src/blop/engine/regression.py`](../src/blop/engine/regression.py) and [`src/blop/tools/results.py`](../src/blop/tools/results.py).
- Recording already persists semantic locator hints such as ARIA role/name, label text, and test-id-derived selectors in [`src/blop/engine/recording.py`](../src/blop/engine/recording.py).
- Context graph and release-scoped reasoning already exist in [`src/blop/engine/context_graph.py`](../src/blop/engine/context_graph.py), [`src/blop/tools/journeys.py`](../src/blop/tools/journeys.py), [`src/blop/tools/release_check.py`](../src/blop/tools/release_check.py), and [`src/blop/tools/triage.py`](../src/blop/tools/triage.py).

The implication is important: BLOP does **not** need a wholesale rewrite inspired by these projects. The highest-value work is in **hardening, narrowing, and productizing** the capabilities it already has.

## Prioritized matrix

| Priority | Project | What it does well | BLOP overlap today | Recommendation | What BLOP should replicate | What BLOP should not copy |
| --- | --- | --- | --- | --- | --- | --- |
| P0 | [microsoft/playwright-mcp](https://github.com/microsoft/playwright-mcp) | Accessibility-snapshot-first browser control, stable page snapshot contract, session/tracing ergonomics | Strong overlap in snapshots, refs, DOM fallback, storage-state handling, and evidence capture | **Adopt** | Tighten BLOP's accessibility snapshot contract, stable element refs, partial snapshot capture, configurable test-id attribute support, and opt-in trace/video ergonomics | Do not copy the full generic browser-control surface as BLOP's primary UX |
| P0 | [browser-use/vibetest-use](https://github.com/browser-use/vibetest-use) | Simple multi-agent fan-out QA sweep across a single URL | Partial overlap via parallel discovery and replay concurrency | **Adapt** | Add a bounded multi-agent smoke sweep for early anomaly discovery before or alongside release preflight | Do not replace recorded-journey replay with pure swarm exploration |
| P1 | [tinyfish-io/agentql](https://github.com/tinyfish-io/agentql) + [agentql-mcp](https://github.com/tinyfish-io/agentql-mcp) | Natural-language element/data queries with structured extraction and self-healing semantics | Partial overlap via semantic locator capture and ARIA-based assertion context | **Adapt** | Add a BLOP-native semantic query/assertion layer for structured evidence extraction and resilient assertions | Do not turn BLOP into a standalone query language platform |
| P1 | [MarcusJellinghaus/mcp-tools-py](https://github.com/MarcusJellinghaus/mcp-tools-py) | LLM-friendly normalization of raw tool output | Partial overlap in reporting, triage, and health-event summarization | **Adopt** | Introduce an explicit evidence-normalization layer before classification and triage | Do not dump raw browser/network/console output directly into higher-level reasoning paths |
| P1 | [r-huijts/mcp-server-tester](https://github.com/r-huijts/mcp-server-tester) | MCP server contract validation | Very limited overlap today | **Adopt** | Add MCP-surface regression tests for canonical tools, resources, and error envelopes | Do not rely only on unit tests for internal engines when the public MCP contract is the product surface |
| P2 | [executeautomation/mcp-playwright](https://executeautomation.github.io/mcp-playwright/docs/intro) | Combined browser + API testing surface with practical utility breadth | Limited overlap today | **Adapt selectively** | Add narrow API/request verification only where it strengthens critical-journey evidence or backend dependency validation | Do not become a general-purpose API MCP toolbox |
| P3 | [hyperbrowserai/mcp](https://github.com/hyperbrowserai/mcp) | Multi-backend browser-agent routing and profile management | Weak overlap today | **Avoid for OSS core** | Reuse only the idea of explicit backend boundaries and profile lifecycle hygiene | Do not add multi-backend routing until BLOP can preserve deterministic evidence quality and auth provenance |
| P3 | [hyperbrowserai/HyperAgent](https://github.com/hyperbrowserai/HyperAgent) | Agent abstraction over MCP tools | Weak overlap today | **Avoid for OSS core** | Borrow orchestration ideas only if BLOP later exposes a higher-level planner agent | Do not reframe BLOP as an agent shell over arbitrary tools |
| P4 | [securityfortech/secops-mcp](https://github.com/securityfortech/secops-mcp) | Unified wrapper for many specialized tools with consistent JSON output | Limited overlap today | **Avoid for OSS core** | Reuse only the normalization pattern for future adjunct scanners | Do not ship a mega-surface for security/perf/accessibility before the release-confidence core is hardened |
| P4 | [ingpoc/ui-test-generation-mcp](https://github.com/ingpoc/ui-test-generation-mcp) | Test generation, failure analysis, and healing as separate tools | Partial overlap in recording, healing, and failure interpretation | **Adapt selectively** | Borrow ideas for human-readable failure-healing analysis and manual-step conversion where it helps recording refresh workflows | Do not pivot toward code-generation-first UX |

## Strongest adoption targets

### 1. Accessibility snapshot contract and locator stability

**Reference:** [microsoft/playwright-mcp](https://github.com/microsoft/playwright-mcp)

**Why this matters**

BLOP already treats accessibility state as the primary low-token representation of the page. That is the right architectural bet. The gap is not direction; it is product sharpness and operator ergonomics.

**Replicate now**

- A stricter, documented snapshot contract with stable element references and predictable markdown/text rendering.
- Partial snapshot capture rooted at a selector or subtree for lower-token targeted assertions.
- Configurable test-id attribute support so replay and recording can prefer stable app-specific hooks.
- Cleaner opt-in controls around trace capture, session save/restore, and artifact policy.
- A clearer contract for when BLOP uses accessibility data, when it falls back to DOM extraction, and how that degrades replay trust.

**BLOP module target**

- Evolve [`src/blop/engine/snapshot_refs.py`](../src/blop/engine/snapshot_refs.py) into the canonical stable-ref contract.
- Extend [`src/blop/engine/snapshots.py`](../src/blop/engine/snapshots.py) and [`src/blop/engine/page_state.py`](../src/blop/engine/page_state.py) with partial-capture and stable rendering behaviors.
- Feed the same contract consistently through recording, assertions, atomic browser tools, and replay repair.

**Opinionated take**

This is the single highest-leverage place to copy industry behavior. It strengthens nearly every BLOP workflow without changing the product thesis.

### 2. Multi-agent smoke sweep as preflight, not as the product

**Reference:** [browser-use/vibetest-use](https://github.com/browser-use/vibetest-use)

**Why this matters**

Vibetest gets real value from cheap parallel exploration. BLOP should use that pattern, but only as a **bounded smoke sweep** that feeds release confidence, not as a replacement for recorded journeys.

**Replicate now**

- A small, bounded fan-out mode that explores the app or seed surfaces with multiple workers/agents looking for obvious blockers.
- Aggregation that rolls multiple smoke findings into a single structured preflight result.
- Explicit separation between `smoke anomalies` and `release-gate failures`.

**BLOP module target**

- Add a `fan_out_smoke` execution path as a sub-mode of discovery or preflight rather than as a new primary workflow.
- Reuse existing section-aware discovery scheduling in [`src/blop/engine/discovery.py`](../src/blop/engine/discovery.py).
- Reuse evidence and reporting infrastructure instead of inventing a separate result format.

**Opinionated take**

This should be framed as "find obvious breakage earlier" rather than "let many agents decide release confidence." BLOP wins when it remains evidence-governed and journey-aware.

### 3. Semantic query/assertion layer

**Reference:** [tinyfish-io/agentql](https://github.com/tinyfish-io/agentql), [tinyfish-io/agentql-mcp](https://github.com/tinyfish-io/agentql-mcp)

**Why this matters**

BLOP already captures semantic hints during recording. The missing piece is a first-class, narrow layer for asking the page for structured facts in business language.

**Replicate now**

- A structured extraction/query primitive for assertions like "checkout confirmation total", "plan tier shown", or "workspace name visible".
- Query-backed assertions that survive DOM reshaping better than brittle selectors.
- A shared abstraction for semantic extraction in replay, debug, and triage.

**BLOP module target**

- Add a native semantic query/assertion module, not a public query-language product.
- Use it to strengthen assertion generation, post-step verification, and release brief evidence.

**Opinionated take**

This is the best way to raise BLOP's semantic reliability without broadening scope too far. It is more aligned with release confidence than generic scraping.

### 4. Evidence normalization before classification

**Reference:** [MarcusJellinghaus/mcp-tools-py](https://github.com/MarcusJellinghaus/mcp-tools-py)

**Why this matters**

Raw browser output is noisy. BLOP already has strong reporting and triage, but it still benefits from an explicit normalization layer that converts raw artifacts into concise, reasoning-ready summaries.

**Replicate now**

- Normalize console/network/assertion/screenshot/trace evidence into a compact, typed summary before classifier or triage stages consume it.
- Preserve provenance so normalized evidence still points back to raw artifacts.
- Separate `signal extraction` from `LLM interpretation`.

**BLOP module target**

- Add an evidence-normalization layer between replay output and classifier/triage/reporting.
- Reuse current reporting hooks in [`src/blop/tools/results.py`](../src/blop/tools/results.py) and [`src/blop/tools/triage.py`](../src/blop/tools/triage.py).

**Opinionated take**

This is quieter than the more glamorous agent features, but it is exactly the kind of boring infrastructure that improves decision quality.

### 5. MCP contract test harness

**Reference:** [r-huijts/mcp-server-tester](https://github.com/r-huijts/mcp-server-tester)

**Why this matters**

BLOP is sold through an MCP surface. If that surface regresses, internal engine correctness is not enough.

**Replicate now**

- Regression tests for canonical tool discovery, response envelopes, required arguments, error behavior, and resource availability.
- Tests for the release-confidence happy path and failure-path contracts.
- Stable assertions around deprecated alias behavior vs canonical surface behavior.

**BLOP module target**

- Add black-box MCP contract tests that exercise `validate_release_setup`, `discover_critical_journeys`, `record_test_flow`, `run_release_check`, and `triage_release_blocker`.

**Opinionated take**

This is not optional engineering hygiene. For an MCP-native product, the MCP contract is part of the shipped interface.

## Selective adaptations only

### executeautomation/mcp-playwright

Keep the useful idea, reject the product shape.

Recommended adaptation:

- Add API/request verification only when it explains browser outcomes for critical journeys.
- Use it for dependency evidence like failed checkout APIs, auth endpoints, or pricing fetches tied to a journey.

Do not:

- add a broad API toolbox,
- mirror a large browser utility surface,
- or split BLOP's story across unrelated testing modalities.

### ingpoc/ui-test-generation-mcp

Recommended adaptation:

- Improve failure-healing explanations and possibly manual-step-to-recording helpers.

Do not:

- center BLOP on code generation,
- or shift operator UX toward generated Playwright/Cypress/Selenium files as the main outcome.

## Avoid in OSS core

### Multi-backend routing

**References:** [hyperbrowserai/mcp](https://github.com/hyperbrowserai/mcp), [hyperbrowserai/HyperAgent](https://github.com/hyperbrowserai/HyperAgent)

Avoid this in OSS core for now.

Why:

- It increases nondeterminism.
- It complicates auth/session provenance.
- It weakens artifact comparability across runs.
- It encourages "pick the smartest agent" thinking over "produce auditable release evidence."

If BLOP ever adds backend routing, it should come later, behind strict evidence and reproducibility constraints.

### Mega-surface tool wrapping

**Reference:** [securityfortech/secops-mcp](https://github.com/securityfortech/secops-mcp)

Avoid a broad security/performance/accessibility wrapper suite in OSS core.

Why:

- BLOP already risks looking broader than it is.
- The product moat is not "one MCP server with many scanners."
- Every adjacent tool surface competes with hardening the release-confidence loop.

## Recommended module roadmap

### Wave 1

- **Snapshot and locator contract hardening**
  - Stable refs
  - Partial snapshot capture
  - Configurable test-id attribute support
  - Explicit artifact/session ergonomics
- **Evidence normalization layer**
  - Reasoning-ready summaries for classifier, triage, and release brief generation
- **MCP contract test harness**
  - Black-box tests for canonical tools and resources

### Wave 2

- **Semantic query/assertion layer**
  - Structured fact extraction for resilient assertions and briefs
- **Multi-agent smoke aggregator**
  - Bounded anomaly sweep feeding preflight/discovery, not replacing replay

### Wave 3

- **Journey-scoped API dependency verification**
  - Only where it materially improves root-cause evidence for critical journeys

## Candidate public API and interface additions

These are the additions worth evaluating because they strengthen the canonical loop without changing BLOP's identity.

| Candidate | Why it is worth adding | Boundary to preserve |
| --- | --- | --- |
| `test_id_attribute` configuration | Makes locators and replay more stable on modern apps | Keep it as a stability aid, not a requirement for BLOP adoption |
| Partial snapshot capture | Reduces token cost and improves focused assertions/debugging | Keep the accessibility-first contract primary |
| Saved storage-state restore/export ergonomics | Improves session portability and operator workflows | Keep auth provenance explicit in results |
| Opt-in trace/video capture policy | Gives better evidence without bloating default runs | Keep artifacts policy-driven, not always-on |
| Fan-out smoke mode | Finds obvious regressions early | Keep release gates based on recorded journeys and evidence-backed replay |

## Test plan for the recommended modules

### Core operating scenarios

Every recommended module should be validated against:

1. Anonymous marketing site
2. Authenticated SaaS dashboard
3. Heavy SPA/editor surface
4. Flaky auth/session recovery path

### Snapshot and locator hardening

Test:

- selector drift,
- test-id present and absent,
- ARIA-rich pages,
- accessibility snapshot partial failure with DOM fallback,
- partial subtree snapshot capture,
- artifact capture policy interactions.

Success condition:

- Replay remains explainable, and degraded trust is surfaced explicitly when fallback paths are used.

### Multi-agent smoke sweep

Test:

- anomaly discovery coverage vs single-thread discovery,
- artifact volume and storage pressure,
- duplicate finding aggregation,
- impact on release-gate determinism.

Success condition:

- Smoke sweep increases early signal without redefining pass/fail release decisions.

### Semantic extraction and assertions

Test:

- success-state confirmations,
- billing/pricing assertions,
- post-submit confirmation states,
- small DOM/UI copy changes across the same business journey.

Success condition:

- Assertions become more resilient to superficial UI change without becoming opaque.

### MCP contract harness

Test:

- tool discovery and schemas,
- canonical happy paths,
- auth failure behavior,
- invalid-argument envelopes,
- resource availability,
- legacy alias stability where compatibility is promised.

Success condition:

- BLOP's public MCP contract becomes regression-tested independently of internal engine refactors.

## Final stance

If BLOP copies one project deeply, it should copy the **discipline** of [microsoft/playwright-mcp](https://github.com/microsoft/playwright-mcp): accessibility-first contracts, stable references, and clean operator ergonomics.

If BLOP borrows one pattern opportunistically, it should borrow the **bounded fan-out smoke sweep** from [browser-use/vibetest-use](https://github.com/browser-use/vibetest-use) without letting that pattern redefine the product.

If BLOP adds one new intelligence layer, it should be a **semantic query/assertion module** inspired by [AgentQL](https://github.com/tinyfish-io/agentql), but shaped around release evidence rather than generic extraction.

Everything else is secondary to one principle:

**BLOP should get better at producing auditable release decisions, not broader at doing browser tricks.**
