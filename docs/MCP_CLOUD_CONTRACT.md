# Blop MCP / HTTP contract (local OSS ↔ hosted cloud)

This document is the canonical parity checklist for ingesting local `blop-mcp` runs into a hosted control plane without field renames.

## Run lifecycle

- **Statuses:** `queued`, `running`, `waiting_auth`, `completed`, `failed`, `cancelled`
- **Release decision strings:** `SHIP`, `INVESTIGATE`, `BLOCK` (from run recommendation / release brief)

## Core models (Pydantic)

- `RunStartedResult`: includes `run_id`, `status`, `flow_count`, `artifacts_dir`, optional `replay_worker_count`, `flow_ids`
- `FailureCase`: includes optional `failure_taxonomy` (`SELECTOR_DRIFT`, `TIMING`, `AUTH_EXPIRED`, `ENV_MISMATCH`, `GENUINE_REGRESSION`, `FLAKE`, `UNKNOWN`) alongside legacy `failure_class`
- `HealedStep`: optional `heal_strategy`, `heal_confidence` for auto-heal audit

## MCP resources

- `blop://health`
- `blop://journeys`
- `blop://release/{release_id}/brief`
- `blop://release/{release_id}/artifacts`
- `blop://release/{release_id}/incidents`
- `blop://run/{run_id}/artifact-index` (canonical)
- `blop://run/{run_id}/artifacts` (alias → same payload as artifact-index)

## HTTP `/v1` authentication

1. When `BLOP_HTTP_API_KEY` is set: `Authorization: Bearer <key>` or `X-Blop-Api-Key: <key>`
2. When `BLOP_HTTP_WORKSPACE_TOKEN` is set: also require `X-Blop-Workspace-Token: <token>`; if `BLOP_HTTP_WORKSPACE_ID` is set, require `X-Blop-Workspace-Id` to match

Errors use HTTP 401/403 with a JSON `detail` object including `blop_code` (e.g. `BLOP_HTTP_INVALID_WORKSPACE_TOKEN`).

## Environment: a11y-first capture

- `BLOP_A11Y_FIRST_EVIDENCE=true` tightens default screenshot capture (navigation/step/periodic) unless explicit `BLOP_CAPTURE_*` overrides are set.

## Hosted Blop Cloud sync (API token)

- `POST /api/v1/sync/runs` — ingest run; response includes `test_run_id` (UUID string).
- `POST /api/v1/sync/runs/{test_run_id}/artifacts` — single artifact reference (`artifact_type`, `artifact_key`, `storage_url`).
- `POST /api/v1/sync/runs/{test_run_id}/artifacts/batch` — up to **100** references per request; `blop-mcp` `SyncClient.push_artifacts` uses this endpoint and chunks larger lists.
