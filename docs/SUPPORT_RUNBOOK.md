# Support runbook (operators and maintainers)

## Before opening an issue

1. **`validate_release_setup`** — confirms API key, Chromium, DB paths, optional `app_url` / auth.
2. **`get_mcp_capabilities`** — confirms tool surface matches expectations (compat tools off unless intended).
3. Re-read the relevant bucket in **`docs/operator_failures.md`**.

## Information to collect

Include (redact secrets):

| Field | Where to find it |
|-------|------------------|
| `blop-mcp` version | `pip show blop-mcp` or `uv pip list` |
| Python version | `python -V` |
| OS | `uname -a` or Windows version |
| Capability profile | `BLOP_CAPABILITIES` / `BLOP_CAPABILITIES_PROFILE` |
| **request_id** | Present on many tool responses after recent server versions |
| **blop_error.code** | Structured error object next to string `error` |
| run_id / release_id | From `run_release_check` or `get_test_results` |

### Redact

- API keys, `storage_state` file contents, cookies, passwords
- Full page screenshots unless the report is private and policy allows

### Safe bundle (example)

- Last 200 lines of `BLOP_DEBUG_LOG` with secrets stripped
- `validate_release_setup` JSON (redacted)
- One failing `get_test_results` payload (truncate `cases` if huge)

## Log location

- **`BLOP_DEBUG_LOG`**: JSON lines; ensure disk space and log rotation on long-lived runners.
- **SQLite**: `BLOP_DB_PATH` — if corrupted, restore from backup or reset after exporting needed flows (operator decision).

## Escalation

- **OSS users:** GitHub Issues with the template fields above.
- **Security:** see **`SECURITY.md`** (no public exploit posts).

## Common fixes

| Symptom | First action |
|---------|----------------|
| `waiting_auth` | `capture_auth_session` or refresh profile; `validate_release_setup` |
| Stale replay | `record_test_flow` for affected journey |
| `BLOP_STORAGE_*` errors | Check disk space, permissions, `BLOP_DB_PATH` |
| LLM quota / rate limit | Retry with backoff; reduce parallel runs; alternate provider env |
