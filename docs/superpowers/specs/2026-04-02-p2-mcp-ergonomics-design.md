# P2 MCP Ergonomics — Cohesion Layer Design

**Date:** 2026-04-02
**Status:** Approved
**Roadmap phase:** P2 — Run lifecycle and MCP ergonomics
**References:**
- [`docs/roadmap/IMPLEMENTATION_PHASES.md`](../../roadmap/IMPLEMENTATION_PHASES.md)
- [`docs/TARGET_PRODUCT_2026.md`](../../TARGET_PRODUCT_2026.md)
- [`docs/superpowers/specs/2026-03-31-pipeline-engine-design.md`](2026-03-31-pipeline-engine-design.md)

---

## Problem statement

blop-mcp's tools work correctly end-to-end (validated against production credentials, April 2026). The failure mode is experiential: the workflow feels like assembled primitives rather than a production tool. Three root causes:

1. **Agent confusion** — after `run_release_check` returns a `run_id`, a naive agent has no instructions in the response about what to do next; it hangs, retries, or aborts.
2. **Silent progress** — `discover_critical_journeys` runs for 30–40s and `run_release_check` polls for 60–120s with no visible feedback; sessions look frozen.
3. **Fragile reconnect** — if the MCP client disconnects mid-run, background tasks may remain in `running`/`queued` state permanently; `get_test_results` returns stale status.

---

## North star

A Cursor or Claude Code agent calling `run_release_check` cold — with no pre-loaded polling instructions — should complete the full release check workflow correctly, with visible progress, and recover correctly if the session drops.

---

## Architecture: three independent increments

Delivered in sequence A → B → C3. Each increment ships value independently.

```
A  Self-sufficient responses   (pure response shape, no protocol changes)
     ↓
B  Progress notifications      (FastMCP ctx threading, engine callbacks)
     ↓
C3 Run resource + orphan policy (new MCP resource + DB hardening)
```

---

## Increment A — Self-sufficient tool responses

### Goal

Every tool that queues background work must tell the caller what to do next, without requiring pre-loaded instructions.

### New model: `WorkflowHint`

Location: `src/blop/mcp/envelope.py`

```python
class WorkflowHint(BaseModel):
    next_action: str
    # Human-readable imperative: "call get_test_results(run_id=...) every 3-5s
    # until status is 'completed', 'failed', or 'cancelled'"

    poll_recipe: dict
    # Structured machine-readable form:
    # {
    #   "tool": "get_test_results",
    #   "args_template": {"run_id": "<run_id>"},
    #   "terminal_statuses": ["completed", "failed", "cancelled"],
    #   "interval_s": 4,
    #   "timeout_s": 900
    # }

    estimated_duration_s: tuple[int, int] | None
    # (min_s, max_s) range based on flow count and mode.
    # None when unknown (e.g. first run with no history).

    progress_hint: str
    # "typically 1–3 min for a 5-flow replay"
```

`WorkflowHint` is added as an optional top-level field `workflow` on tool responses. It is additive — existing fields are unchanged.

### Affected tools

| Tool | Change |
|------|--------|
| `run_release_check` | Full `WorkflowHint`: poll recipe, duration estimate from flow count × avg step time |
| `run_regression_test` | Full `WorkflowHint`: same shape |
| `record_test_flow` | Light hint: `next_action: "flow recorded — run replay with run_release_check or browse flows at blop://journeys"` |
| `discover_critical_journeys` | Light hint: `progress_hint` + `next_action` pointing at `blop://journeys` |

### Duration estimation

`run_release_check` and `run_regression_test` compute `estimated_duration_s` from:
- `flow_count × 18s` as the baseline per flow (empirical from production runs)
- `min = flow_count × 10`, `max = flow_count × 45` as the range
- Falls back to `(30, 300)` when flow count is unknown at queue time

### Files

| File | Action |
|------|--------|
| `src/blop/mcp/envelope.py` | Add `WorkflowHint` model |
| `src/blop/tools/release_check.py` | Add `workflow` field to queued response |
| `src/blop/tools/regression.py` | Add `workflow` field to queued response |
| `src/blop/tools/record.py` | Add light `workflow` hint |
| `src/blop/tools/journeys.py` | Add light `workflow` hint |
| `tests/test_workflow_hint.py` | Unit tests: model shape, tool response fields |

---

## Increment B — MCP progress notifications

### Goal

Long-running tools emit `notifications/progress` during execution. Clients that support `progressToken` (Cursor, Claude Code) show a live indicator. Clients that don't are unaffected.

### Design principle: engine stays protocol-agnostic

The engine (`RunPipeline`, `run_flows`, `discovery.py`) must not import FastMCP or know about MCP. Progress is passed in as an optional async callback:

```python
ProgressCallback = Callable[[int, int, str], Awaitable[None]] | None
# Args: (current: int, total: int, message: str)
```

Tool handlers create the callback as a closure over FastMCP's `ctx`:

```python
async def run_release_check(..., ctx: Context):
    async def progress(current: int, total: int, message: str) -> None:
        await ctx.report_progress(current, total)
    result = await pipeline.run(ctx=run_ctx, progress_callback=progress)
```

When `ctx` is not available or `progressToken` was not supplied, the callback is `None` and all progress calls are no-ops.

### Stage weights (total = 100)

| Stage | Weight | Tick points |
|-------|--------|-------------|
| VALIDATE | 5 | On entry, on completion |
| AUTH | 10 | On entry, on completion |
| EXECUTE | 70 | Per flow completed (subdivided by flow count) |
| CLASSIFY | 10 | On entry, on completion |
| REPORT | 5 | On completion |

Example: 5-flow replay — each flow completion emits `+14` units (70 / 5).

### Discovery progress

`discover_critical_journeys` emits one tick per page crawled:

```
progress(current=pages_crawled, total=max_pages, message=f"crawled {url}")
```

### Recording progress

`record_test_flow` emits one tick per step captured:

```
progress(current=step_index, total=estimated_max_steps, message=f"step {step_index}: {action_type}")
```

`estimated_max_steps` defaults to the `max_steps` parameter (or 20 if unset).

### Files

| File | Action |
|------|--------|
| `src/blop/engine/pipeline.py` | Accept `progress_callback` in `RunPipeline.run()`, call at each stage boundary |
| `src/blop/engine/stages/execute.py` | Call `progress_callback` per flow completed |
| `src/blop/engine/discovery.py` | Accept and call `progress_callback` per page |
| `src/blop/engine/recording.py` | Accept and call `progress_callback` per step |
| `src/blop/tools/release_check.py` | Wire `ctx.report_progress` → callback |
| `src/blop/tools/regression.py` | Wire `ctx.report_progress` → callback |
| `src/blop/tools/journeys.py` | Wire `ctx.report_progress` → callback |
| `src/blop/tools/record.py` | Wire `ctx.report_progress` → callback |
| `tests/test_progress_callback.py` | Unit tests: callback fires at correct points, no-op when None |

### MCP library version

`ctx.report_progress(current, total)` is confirmed available in `mcp==1.26.0` (the version pinned in this repo via `mcp[cli]>=1.0.0`). Uses `from mcp.server.fastmcp import Context`. No version guard needed.

---

## Increment C3 — Run resource + orphan policy

### Goal

Any client that reconnects after a disconnect can immediately recover run state. Background tasks never leave runs permanently stuck in `running` or `queued`.

### New MCP resource: `blop://runs/{run_id}`

Location: registered in `src/blop/server.py`, implemented in `src/blop/tools/resources.py`.

Response shape:

```json
{
  "run_id": "...",
  "status": "running | completed | failed | cancelled | interrupted",
  "release_id": "...",
  "app_url": "...",
  "flow_count": 5,
  "failed_count": 2,
  "started_at": "2026-04-02T10:00:00Z",
  "updated_at": "2026-04-02T10:02:34Z",
  "workflow": {
    "next_action": "call get_test_results(run_id=...) every 3-5s until terminal",
    "poll_recipe": { ... },
    "progress_hint": "run is in progress"
  }
}
```

If `status` is already terminal, `workflow` contains `next_action: "read blop://release/{release_id}/brief for the decision"`.

Implementation: pure SQLite read from `runs` table. No engine imports. Response time target: < 50ms.

### Orphan policy

**On disconnect / task cancellation:**

Wrap background tasks in `run_release_check` and `run_regression_test` with:

```python
try:
    await _execute_run(run_id, ...)
except asyncio.CancelledError:
    await db.update_run_status(run_id, "interrupted", note="MCP session disconnected")
    raise
except Exception as e:
    await db.update_run_status(run_id, "failed", note=str(e))
    raise
```

**On server startup:**

`init_db()` sweep — any run with `status IN ('running', 'queued')` and `updated_at < now() - 600s` is marked `interrupted` with note `"found stale on startup"`. This cleans up runs from previous sessions that never received a cancellation signal.

**`get_test_results` contract update:**

`get_test_results` must return `interrupted` as a valid terminal status so poll loops exit cleanly. Add `"interrupted"` to the documented terminal status set alongside `completed`, `failed`, `cancelled`.

### Files

| File | Action |
|------|--------|
| `src/blop/tools/resources.py` | Add `run_resource(run_id)` handler |
| `src/blop/server.py` | Register `blop://runs/{run_id}` resource |
| `src/blop/storage/sqlite.py` | Add `update_run_status(run_id, status, note)` helper; add startup stale-run sweep to `init_db()` |
| `src/blop/tools/release_check.py` | Wrap background task with CancelledError handler |
| `src/blop/tools/regression.py` | Wrap background task with CancelledError handler |
| `tests/test_run_resource.py` | Resource shape, terminal vs in-progress workflow hint |
| `tests/test_orphan_policy.py` | CancelledError → interrupted, startup sweep |

---

## Error handling

- `WorkflowHint` is optional in all responses. If computation fails (e.g. flow count unavailable), omit it rather than error.
- `progress_callback` failures are swallowed silently (log at DEBUG). Never let a progress tick failure abort a run.
- `blop://runs/{run_id}` returns a structured `{"error": "run_not_found", "run_id": "..."}` for unknown IDs rather than raising.

---

## Testing strategy

| Layer | What |
|-------|------|
| Unit | `WorkflowHint` shape + tool response fields (A); callback fire count and no-op when None (B); resource shape + stale sweep (C3) |
| Integration | Full pipeline with mock progress sink — assert N callbacks fired for N flows (B); `CancelledError` → `interrupted` in DB (C3) |
| Smoke | Manual: call `run_release_check` cold from a fresh Claude Code session without pre-loaded instructions, verify agent polls correctly (A) |

---

## Out of scope

- HTTP/SSE side channel (`blop-http`) — already exists, not part of this spec
- `BrowserContextFactory` consolidation — P3
- Healing layers — P4
- Cross-run analytics, hosted sync — P2+/hosted

---

## Acceptance criteria

**A:**
- `run_release_check` response includes `workflow.poll_recipe` with correct tool name, args template, terminal statuses, and interval
- A fresh agent calling `run_release_check` with no system prompt polling instructions completes the full workflow correctly

**B:**
- `discover_critical_journeys` emits ≥ 1 progress tick per page crawled when `progressToken` is present
- `RunPipeline` emits progress at each stage boundary
- No progress-tick failure can abort a run

**C3:**
- `blop://runs/{run_id}` returns current status in < 50ms
- A run cancelled via `CancelledError` transitions to `interrupted` in the DB
- Startup sweep marks stale runs (stuck > 10 min) as `interrupted`
- `get_test_results` treats `interrupted` as a terminal status
