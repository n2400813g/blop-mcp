# MCP tool performance (DemoBlaze benchmark)

This measures **in-process wall time** for the same Python code paths the MCP server uses when tools run (context reads, optional preflight, Playwright navigation + snapshot). It does **not** include JSON-RPC encoding or stdio round-trip latency from the IDE to `blop-mcp`.

## Prerequisites

- Dependencies installed (`uv pip install -e .` or equivalent).
- Chromium for Playwright (`playwright install chromium`) when using the browser phase.
- Network access to `https://www.demoblaze.com` for browser and `validate_release_setup` phases.

## Run

From the repo root:

```bash
uv run python scripts/benchmark_mcp_demoblaze.py
```

### Options

| Flag | Meaning |
|------|---------|
| `--iterations N` | Timed samples per phase after warmup (default 15). |
| `--warmup N` | Browser iterations discarded before timing (default 2). |
| `--skip-browser` | Only time context tools + optional validate (no Playwright). |
| `--skip-context` | Only browser (and validate if set). |
| `--validate` | Time `validate_release_setup(app_url=demoblaze)` each iteration (HTTP + local checks). |
| `--json` | Machine-readable output. |

### Reading results

- **Context block:** `get_workspace_context_cold_ms` / `get_ux_taxonomy_cold_ms` reflect cache misses; `*_cached` stats reflect steady-state calls (typically sub-millisecond after warm caches).
- **`get_journeys_for_release`:** Depends on SQLite + `journeys_resource()` workload (scales with recorded flows).
- **`browser_navigate_plus_snapshot`:** Dominated by TLS, DemoBlaze TTFB, and DOM evaluation; **p95** is usually more stable than **mean** under jitter.
- **`validate_release_setup`:** Adds HTTP reachability to the target URL plus local environment checks.

### Real-time MCP (stdio) — agent-style navigation

The in-process benchmark does not exercise JSON-RPC over stdio. For a **live** test that spawns `blop-mcp` the same way Cursor does and calls tools over the wire:

```bash
# Full chain: initialize → get_workspace_context → navigate_to_url → get_page_snapshot
uv run python scripts/mcp_stdio_e2e_demoblaze.py

# Optional second hop: click an element by snapshot ref, then snapshot again
uv run python scripts/mcp_stdio_e2e_demoblaze.py --click-ref e3

# Another site
uv run python scripts/mcp_stdio_e2e_demoblaze.py --url https://example.com/

# Context only (no Playwright)
uv run python scripts/mcp_stdio_e2e_demoblaze.py --skip-browser

# Machine-readable timings + payloads
uv run python scripts/mcp_stdio_e2e_demoblaze.py --json
```

Timings printed include `initialize_s` and per-tool `tool_<name>_s` (round-trip from client through stdio to handler and back). Inherits your current shell environment (e.g. API keys) like a normal MCP host.

**Using Cursor as the “agent”:** configure the blop MCP server in the IDE, then ask the assistant to call `navigate_to_url` and `get_page_snapshot` against DemoBlaze; behavior matches the script’s tool sequence.

### Live Snapshot (2026-03-27)

Latest local benchmark sample from this repo on one developer machine:

- In-process benchmark (`uv run python scripts/benchmark_mcp_demoblaze.py --validate --json`):
  `journeys_resource` p50/p95 `1.89/2.58 ms`, `validate_release_setup` p50/p95 `416.8/763.7 ms`, replay startup p50/p95 `2.03/2.87 ms`, 8-flow replay finalize overhead p50/p95 `18.37/26.84 ms`, warm `navigate_to_url + get_page_snapshot` p50/p95 `13.35/16.49 ms`.
- Repeated stdio samples used `n=15` subprocess runs per mode.

| Mode | initialize p50 / p95 | workspace p50 / p95 | navigate p50 / p95 | snapshot p50 / p95 | perform_step p50 / p95 | post-click snapshot p50 / p95 | total session p50 / p95 | Notes |
|------|----------------------|---------------------|--------------------|--------------------|------------------------|-------------------------------|-------------------------|-------|
| `--skip-browser` | `333.8 / 402.3 ms` | `2.48 / 3.04 ms` | n/a | n/a | n/a | n/a | `391.8 / 465.1 ms` | Good transport baseline for cold stdio sessions. |
| full DemoBlaze | `341.4 / 458.6 ms` | `2.43 / 5.30 ms` | `686.8 / 1552.3 ms` | `6.52 / 19.41 ms` | n/a | n/a | `1126.5 / 1992.8 ms` | Tail latency is dominated by live site/browser navigation. |
| `--click-ref e3` | `320.4 / 389.4 ms` | `2.35 / 2.57 ms` | `1471.2 / 3602.8 ms` | `5.93 / 8.90 ms` | `49.98 / 79.48 ms` | `3.15 / 17.61 ms` | After fixing compat selector generation, `perform_step` succeeded `15/15` times on DemoBlaze `Contact` (`e3`). Tail latency in this chain is now back to live navigation, not click resolution. |

## CI / pytest

Gated test (skipped unless `RUN_DEMOBLAZE_BENCH=1`; marked `integration`):

```bash
RUN_DEMOBLAZE_BENCH=1 uv run pytest tests/performance/test_mcp_demoblaze_bench.py -q
```

The default test invokes the script with `--skip-browser` and a temp DB. For a full browser run against DemoBlaze, execute the script manually without `--skip-browser`.

### Stdio E2E pytest (optional)

Spawns a subprocess `uv run blop-mcp` via the MCP client (slower; needs Playwright unless skipped):

```bash
RUN_MCP_STDIO_E2E=1 uv run pytest tests/performance/test_mcp_stdio_e2e.py -q
```
