# Cursor Quickstart

This is the canonical production-style quickstart for Cursor using local managed `stdio`.

## Prerequisites

- Python 3.11+
- `uv`
- Chromium installed with `playwright install chromium --with-deps --no-shell`
- One LLM provider key:
  - `GOOGLE_API_KEY`, or
  - `ANTHROPIC_API_KEY` with `BLOP_LLM_PROVIDER=anthropic`, or
  - `OPENAI_API_KEY` with `BLOP_LLM_PROVIDER=openai`

## Recommended MCP config

```json
{
  "mcpServers": {
    "blop": {
      "command": "uv",
      "args": [
        "--directory",
        "/absolute/path/to/blop-mcp",
        "run",
        "python",
        "-m",
        "blop.server"
      ],
      "env": {
        "BLOP_ENV": "production",
        "BLOP_REQUIRE_ABSOLUTE_PATHS": "true",
        "BLOP_CAPABILITIES_PROFILE": "production_minimal",
        "BLOP_ENABLE_COMPAT_TOOLS": "false",
        "BLOP_DB_PATH": "/absolute/path/to/blop-mcp/.blop/runs.db",
        "BLOP_RUNS_DIR": "/absolute/path/to/blop-mcp/runs",
        "BLOP_DEBUG_LOG": "/absolute/path/to/blop-mcp/.blop/blop.log",
        "GOOGLE_API_KEY": "..."
      }
    }
  }
}
```

## First-run checks

1. Restart Cursor so the MCP server launches cleanly.
2. Read `blop://health`.
3. Run `validate_release_setup(app_url="https://your-app.com")`.
4. If your app needs login, run `capture_auth_session(...)` before replay checks.

## Recommended release-gating loop

1. `discover_critical_journeys(...)`
2. `read blop://journeys`
3. `record_test_flow(...)` for missing or stale release-gating journeys
4. `run_release_check(..., mode="replay")`
5. `get_test_results(...)`
6. `triage_release_blocker(...)` if the decision is not `SHIP`
