# Pipeline Engine + Schema Hardening — Architecture Design

**Date:** 2026-03-31
**Status:** Approved
**Scope:** Internal refactor only — MCP tool names, resource URLs, and public schemas remain backward compatible.
**Target users:** Startups evaluating and adopting blop as a release-confidence control plane.

---

## 1. Problem Statement

Three root causes drive the current architectural pain:

1. **LLM API failures are opaque.** When Gemini/Anthropic/OpenAI fails, runs silently stall or produce empty diagnostics. Agents cannot self-triage or retry intelligently.
2. **Tool schemas are loose.** Implicit parameter coercion and missing descriptions cause LLM clients to guess wrong argument slots, producing parameter-shape mismatches.
3. **The core run loop is a monolith.** `engine/regression.py` (1,987 lines) fuses replay, healing, and classification. No stage can be improved, tested, or replaced independently.

Secondary: `server.py` is 3,382 lines mixing tool registration, resource handlers, and utilities. `config.py` mixes defaults, validation, and env reads. Three overlapping browser session models. Two competing failure taxonomies.

---

## 2. Goals

- Every run step is visible to the user in real time (health stream events)
- Every failure carries an actionable diagnostic (what, why, how to fix, retry-safe)
- Each stage of the run loop is independently testable and replaceable
- All tool schemas use `Literal`/`enum` constraints + `Field` descriptions + examples
- One canonical response envelope across all tools
- No breaking changes to MCP tool names, resource URLs, or existing response fields

---

## 3. Core Pipeline Architecture

Replace `engine/regression.py` with a `RunPipeline` orchestrator plus 5 discrete stages.

### Stage sequence

```
RunPipeline.run(request: RunRequest) -> RunResult
  │
  ├─ Stage 1: VALIDATE   app_url reachable, config sane, flows exist in DB
  ├─ Stage 2: AUTH       resolve + validate auth profile, acquire storage_state
  ├─ Stage 3: EXECUTE    replay steps, capture evidence, invoke healing on failure
  ├─ Stage 4: CLASSIFY   score failures, assign taxonomy + severity + criticality
  └─ Stage 5: REPORT     produce SHIP / INVESTIGATE / BLOCK decision + next actions
```

### Stage contract

Each stage implements:

```python
class BaseStage:
    async def run(self, ctx: RunContext) -> RunContext:
        """Mutates ctx in place, emits events, raises StageError on hard failure."""
```

`RunContext` is a single mutable object passed through the pipeline carrying:
- `run_id`, `request`, `config`
- `validated_url`, `browser_config` (after VALIDATE)
- `auth_state` (after AUTH)
- `step_results: list[StepResult]` (after EXECUTE)
- `classified_cases: list[ClassifiedCase]` (after CLASSIFY)
- `report: RunReport` (after REPORT)
- `events: list[HealthEvent]` (appended by each stage)

Stages never import each other. The pipeline orchestrator owns sequencing.

### New module layout

```
engine/
  pipeline.py              RunPipeline orchestrator + RunContext
  stages/
    validate.py            Stage 1
    auth.py                Stage 2
    execute.py             Stage 3 (replay logic, healing calls)
    classify.py            Stage 4
    report.py              Stage 5
  healing.py               HealingStrategy (extracted from regression.py)
  browser_context.py       BrowserContextFactory (replaces pool + session manager + profile)

server.py                  FastMCP init, startup only (<200 lines)
server_tools.py            All @app.tool() registrations
server_resources.py        All @app.resource() handlers

config/
  core.py                  Required settings (API keys, DB path, app_url)
  optional.py              All tuning knobs (timeouts, concurrency, etc.)
  validate.py              validate_app_url(), check_llm_api_key(), etc.
```

---

## 4. Transparency Layer (Health Stream Events)

Every stage emits typed `HealthEvent` objects into the run health stream (`get_run_health_stream`). The event taxonomy is exhaustive — nothing undocumented.

### Event shape

```python
class HealthEvent(BaseModel):
    run_id: str
    stage: Literal["VALIDATE", "AUTH", "EXECUTE", "CLASSIFY", "REPORT", "PIPELINE"]
    event_type: EventType          # see taxonomy below
    seq: int                       # monotonic, per-run
    timestamp: datetime
    message: str                   # human-readable, always present
    details: dict[str, Any]        # stage-specific, always a dict (never null)
```

### Event taxonomy (Literal, complete)

```
VALIDATE_START  VALIDATE_OK  VALIDATE_FAIL
AUTH_START      AUTH_OK      AUTH_WAITING   AUTH_FAIL
EXECUTE_START   STEP_START   STEP_OK        STEP_FAIL   STEP_HEALED  STEP_SKIP  EXECUTE_DONE
LLM_CALL_START  LLM_CALL_OK  LLM_CALL_FAIL  LLM_CALL_FALLBACK
CLASSIFY_START  CLASSIFY_OK  CLASSIFY_FAIL
REPORT_READY
PIPELINE_ABORT
```

`LLM_CALL_*` events are new. Every LLM invocation emits start, success/failure, and fallback events with: `provider`, `model`, `attempt`, `failure_reason`, `fallback_provider` (if applicable).

`STEP_FAIL` details include: `step_index`, `selector`, `action`, `healing_attempted`, `healing_result`, `screenshot_ref`, `console_errors`.

`PIPELINE_ABORT` attaches the full `StageError` so clients can surface exactly where and why the run terminated.

---

## 5. Error Handling

### StageError

Every stage failure raises `StageError`:

```python
class StageError(Exception):
    stage: Literal["VALIDATE", "AUTH", "EXECUTE", "CLASSIFY", "REPORT"]
    code: str                    # BLOP_<DOMAIN>_<CODE> stable string
    message: str                 # human-readable summary
    likely_cause: str            # plain English explanation
    suggested_fix: str           # concrete next step for the user/agent
    retry_safe: bool             # can this exact call be retried safely?
    details: dict[str, Any]      # raw context (never omitted)
```

### Canonical response envelope

One envelope shape across all tools (no more 3 competing formats):

```python
{
  "ok": bool,
  "data": {...} | null,
  "error": {
    "code": "BLOP_AUTH_PROFILE_NOT_FOUND",
    "message": "Auth profile 'staging' was not found.",
    "likely_cause": "Profile not created yet or created in a different working directory.",
    "suggested_fix": "Run save_auth_profile with profile_name='staging' first.",
    "retry_safe": false,
    "stage": "AUTH",
    "details": {}
  } | null,
  "request_id": "req_abc123",
  "tool_name": "run_regression_test"
}
```

Legacy fields (`blop_error`, `run_id` at top level) are preserved for backward compatibility.

---

## 6. Schema Hardening

Every tool parameter gets:
- `Literal[...]` or `Enum` for all constrained string values
- `Field(description="...", examples=[...])` on every parameter
- Explicit defaults on all optional parameters (no implicit `None`)
- `business_criticality: Literal["revenue", "activation", "retention", "support", "other"]` everywhere it appears

Example (before → after):

```python
# Before
async def run_regression_test(flow_ids: list | None = None, mode: str = "replay"):

# After
async def run_regression_test(
    flow_ids: list[str] | None = Field(
        default=None,
        description="IDs of recorded flows to replay. If omitted, replays all flows for the app.",
        examples=[["flow_abc123", "flow_def456"]]
    ),
    mode: Literal["replay", "record"] = Field(
        default="replay",
        description="replay: replays stored steps. record: re-records flows from scratch.",
    ),
):
```

---

## 7. Browser Context Abstraction

Collapse `BrowserPool` + `BrowserSessionManager` + `BrowserProfile` into one `BrowserContextFactory`:

```python
class BrowserContextFactory:
    async def acquire(
        mode: Literal["isolated", "persistent", "headed"],
        auth_state: AuthState | None = None,
    ) -> ManagedBrowserContext:
        ...

    async def release(ctx: ManagedBrowserContext) -> None:
        ...
```

- `isolated` — new context per task, destroyed after (replaces BrowserPool). Default for regression.
- `persistent` — shared context kept alive between calls (replaces BrowserSessionManager). For compat tools.
- `headed` — headed browser for auth capture and debug reruns.

Internally: one shared `Browser` process per mode (lazy-started), one `BrowserContext` per lease. No behavior change — same isolation semantics, cleaner interface.

---

## 8. Failure Taxonomy Unification

`failure_taxonomy` is canonical going forward. `failure_class` maps to it internally for backward compat.

```python
FailureTaxonomy = Literal[
    "SELECTOR_DRIFT",       # element moved or renamed
    "TIMING",               # SPA settle or network idle fired too early
    "AUTH_EXPIRED",         # storage_state went stale
    "GENUINE_REGRESSION",   # actual product bug
    "FLAKE",                # non-deterministic failure
    "LLM_FAILURE",          # NEW: LLM call failed during execution or classification
    "ENV_ISSUE",            # infra or config problem
]
```

`LLM_FAILURE` is a new taxonomy entry. Previously these failures were either silently swallowed or mis-classified as `FLAKE`.

---

## 9. Implementation Phases

### Phase 1 — Schema hardening + envelope unification (Week 1–2)
- Add `Literal`/`Field` to all tool parameters
- Unify response envelope in `mcp/envelope.py`
- Add `likely_cause` + `suggested_fix` + `retry_safe` to all existing error paths
- Ships immediately, no internal restructuring needed

### Phase 2 — Pipeline engine + module split (Week 3–5)
- Extract `engine/stages/` from `regression.py`
- Implement `RunContext`, `RunPipeline`, `StageError`
- Extract `engine/healing.py`
- All existing tests must pass; behavior unchanged

### Phase 3 — Health stream + LLM events (Week 5–6)
- Implement full event taxonomy in all stages
- Add `LLM_CALL_*` events to `llm_factory.py`
- Add `PIPELINE_ABORT` with attached `StageError`
- Verify with `get_run_health_stream` in live runs

### Phase 4 — Browser context factory + config split (Week 6–7)
- Implement `BrowserContextFactory`
- Split `config.py` into `config/core.py`, `config/optional.py`, `config/validate.py`
- Split `server.py` into `server.py`, `server_tools.py`, `server_resources.py`

---

## 10. Success Criteria

- `validate_setup` failure tells the user exactly which stage failed and the `suggested_fix`
- Every run step appears in `get_run_health_stream` with event_type and human-readable message
- Every LLM call appears as `LLM_CALL_START` / `LLM_CALL_OK|FAIL|FALLBACK` in the stream
- Every tool error includes `likely_cause`, `suggested_fix`, and `retry_safe`
- All existing MCP tool names, resource URLs, and response fields preserved
- All existing tests pass after Phase 2 restructuring
- A startup with only `GOOGLE_API_KEY` + `app_url` can complete a full run with zero support tickets
