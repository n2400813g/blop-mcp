# Compatibility And Deprecation Policy

## Supported baseline

- Primary supported MCP runtime: local managed `stdio`
- Primary supported command: `blop-mcp` (or `blop`)
- Primary supported workflow: `validate_release_setup` -> `discover_critical_journeys` -> `record_test_flow` -> `run_release_check(mode="replay")`

## Compatibility surface

- Canonical release-confidence tools are the long-term stable surface.
- Compatibility and legacy tools may remain available behind capability/profile or compat flags.
- `BLOP_ENABLE_COMPAT_TOOLS=false` is the production default.

## Deprecation rules

- Deprecated tools should return a replacement tool and replacement payload when possible.
- Canonical replacements should be documented before a compat tool is removed.
- Removal should happen no earlier than one stable release after the replacement path is available.

## Runtime expectations

- Python support follows the versions documented in the README.
- MCP clients should be configured with absolute paths in production-style deployments.
- Release-gating decisions should prefer replay mode over targeted or compatibility-only workflows.
