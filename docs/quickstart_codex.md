# Codex-Compatible Quickstart

This guide covers Codex-compatible MCP clients that launch `blop-mcp` as a local process.

## Runtime contract

- Launch `blop` locally through `stdio`
- Use absolute paths for DB, runs, and logs
- Keep `BLOP_ENABLE_COMPAT_TOOLS=false` unless you explicitly need the legacy surface

## Example launch command

```bash
uv --directory /absolute/path/to/blop-mcp run python -m blop.server
```

Recommended environment:

```bash
export BLOP_ENV=production
export BLOP_REQUIRE_ABSOLUTE_PATHS=true
export BLOP_CAPABILITIES_PROFILE=production_minimal
export BLOP_DB_PATH=/absolute/path/to/blop-mcp/.blop/runs.db
export BLOP_RUNS_DIR=/absolute/path/to/blop-mcp/runs
export BLOP_DEBUG_LOG=/absolute/path/to/blop-mcp/.blop/blop.log
export GOOGLE_API_KEY=...
```

## First checks

1. Read `blop://health`
2. Run `validate_release_setup(app_url="https://your-app.com")`
3. Record or refresh any stale release-gating journeys from `blop://journeys`
4. Run `run_release_check(..., mode="replay")`
