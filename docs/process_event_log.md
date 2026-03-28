# Process event log interchange

Blop persists control-plane events in `run_health_events` (see `storage/sqlite.py`). For process mining (variants, conformance, bottlenecks), events are projected to **process event log rows** with:

| Field | Meaning |
|-------|---------|
| `case:concept:name` | Process instance — typically `run_cases.case_id`; run-wide events use `run_scope::{run_id}` when no case is set. |
| `concept:name` | Activity — see **canonical activities** below. |
| `time:timestamp` | ISO-8601 time from the health event `created_at`. |

## Canonical activities (enforced)

Implementation: `blop/reporting/health_event_taxonomy.py`.

| Source | Rule |
|--------|------|
| `replay_step_completed` | `canonical_replay_step_activity(action, status)` → `replay_{action}_{status}` with lowercase snake segments (e.g. `replay_click_pass`, `replay_navigate_fail`). |
| Run lifecycle `event_type` | Stable snake_case name equals `event_type` when listed in `CANONICAL_ACTIVITY_BY_EVENT_TYPE` (e.g. `run_started`, `run_completed`, `run_failed`, `run_cancelled`, `run_checkpointed`, `run_resumed`, `run_waiting_auth`, `run_force_terminated`, `run_startup_timing`, `case_completed`). |
| `auth_landing_observed` | `auth_landing_observed` |
| Unknown `event_type` | Sanitized `event_type` (lowercase, non-alphanumeric → `_`). |

Payload field `activity` on `replay_step_completed`, when present and valid, overrides the default; values are normalized to safe snake_case.

Replay steps emit `replay_step_completed` with payload fields including `activity`, `step_id`, `replay_mode`, `elapsed_ms`, `selector_entropy`, `aria_consistency`.

Optional analysis uses **PM4Py** via the `blop-mcp[insights]` extra (`get_process_insights` tool). The default install does not require PM4Py.

## Optional LLM span export

For OpenTelemetry spans around select LLM calls (repair + vision), set `BLOP_OTEL_TRACING=1` and install `blop-mcp[otel]` (see `blop/engine/llm_tracing.py`). Console span export is intended for local debugging; production setups typically attach an OTLP exporter to the tracer provider.
