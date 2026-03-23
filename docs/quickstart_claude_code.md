# Claude Code Quickstart

This is the canonical Claude Code setup for `blop` using the supported local managed `stdio` baseline.

## Add the MCP server

```bash
claude mcp add blop /absolute/path/to/blop-mcp/.venv/bin/blop-mcp \
  -e BLOP_ENV=production \
  -e BLOP_REQUIRE_ABSOLUTE_PATHS=true \
  -e BLOP_CAPABILITIES_PROFILE=production_minimal \
  -e BLOP_ENABLE_COMPAT_TOOLS=false \
  -e BLOP_DB_PATH=/absolute/path/to/blop-mcp/.blop/runs.db \
  -e BLOP_RUNS_DIR=/absolute/path/to/blop-mcp/runs \
  -e BLOP_DEBUG_LOG=/absolute/path/to/blop-mcp/.blop/blop.log \
  -e GOOGLE_API_KEY=...
```

## Verify the connection

1. Run `/mcp` and confirm `blop` is connected.
2. Read `blop://health`.
3. Run `validate_release_setup(app_url="https://your-app.com")`.

## Recommended usage

- Use `run_release_check(..., mode="replay")` as the main release gate.
- Treat `goal_fallback` as recovery-only when replay drift is too high.
- Use `blop://release/{release_id}/brief` and `blop://release/{release_id}/artifacts` for low-token follow-up.
