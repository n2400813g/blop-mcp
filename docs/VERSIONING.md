# Versioning and API stability

## Semantic versioning

`blop-mcp` uses **SemVer** (`MAJOR.MINOR.PATCH`):

| Bump | When |
|------|------|
| **MAJOR** | Breaking changes to **documented** MCP tool or resource contracts, or breaking changes to default env behavior that require operator action. |
| **MINOR** | New tools, resources, optional fields, backward-compatible behavior, new opt-in env flags. |
| **PATCH** | Bug fixes, docs, non-breaking internal refactors, dependency updates that do not change the public contract. |

## What counts as the public API

- **MCP tools** exposed by default capability profiles (names, required arguments, and **stable keys** in success and error payloads such as `error`, `blop_error`, `run_id`).
- **MCP resources** (`blop://...`) URI templates and JSON shapes documented in-repo.
- **CLI entrypoints** (`blop`, `blop-mcp`, `blop-http`, `blop-inspect`) and their availability.
- **Environment variables** listed in `.env.example` / `deploy/prod.env.template` as supported for production.

Non-guaranteed (may change in minor releases with notice in `CHANGELOG.md`):

- Undocumented keys inside large result blobs.
- Tools behind `BLOP_ENABLE_COMPAT_TOOLS` / legacy aliases.
- Internal Python modules outside the documented extension points.

## SQLite schema

The local database uses **versioned migrations** (`src/blop/storage/sqlite.py`). Upgrading the package should run migrations automatically on startup. A **MAJOR** bump may be required if migrations are not backward-compatible and need manual operator steps (called out in changelog).

## Compatibility with MCP clients

Cursor, Claude Code, and other MCP hosts should pin a **released** `blop-mcp` version in their environment (venv, `uv tool`, or container image). Follow `CHANGELOG.md` for breaking changes when upgrading across **MAJOR** versions.
