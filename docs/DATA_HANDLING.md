# Data handling (local runtime)

This document summarizes **what data blop stores locally**, **why**, and **how to handle it** for security and compliance conversations. It is not legal advice.

## Local-first default

The OSS runtime is **local-first**: by default, evidence and metadata stay on the machine that runs the MCP server (developer laptop, CI agent, or dedicated runner). Optional **hosted sync** (when configured via project/env variables) may transmit run payloads to a separate service; treat that as a distinct data flow governed by your agreement with the operator of that service.

## Categories of data

| Location | Contents | Sensitivity |
|----------|----------|-------------|
| **SQLite** (`BLOP_DB_PATH`, default under `.blop/`) | Run metadata, journey definitions, release briefs, health events, auth profile *references* (paths), optional telemetry-shaped rows | May include **URLs**, **flow names**, **failure text**, **timestamps** |
| **Auth storage state** (paths referenced by profiles) | Playwright `storage_state.json` — cookies, localStorage snapshots | **High** — session equivalence |
| **Runs directory** (`BLOP_RUNS_DIR`) | Screenshots, traces, console/network logs, mobile artifacts | **High** — may contain **PII**, secrets in page content, auth tokens in network captures |
| **Debug log** (`BLOP_DEBUG_LOG`) | Structured JSON logs (tool errors, events) | **Medium** — may include URLs, ids; configure paths outside shared disks in production |
| **LLM providers** | Prompts may include page text, goals, DOM summaries, screenshots (per tool configuration) | **High** — governed by your provider agreement and enterprise AI policy |

## Retention and deletion

- **Remove a run:** delete or archive the run’s rows and artifact folders according to your internal policy (future tooling may add first-class retention APIs).
- **Rotate auth:** re-run `capture_auth_session` or update `save_auth_profile`; remove obsolete `storage_state` files from disk.
- **Nuke local state:** stop the server, delete `BLOP_DB_PATH`, `BLOP_RUNS_DIR`, and `.blop/` contents as applicable, then re-run `validate_release_setup`.

## GDPR / privacy checklist (typical questions)

- **Controller:** Your organization is usually the controller for data about *your* app and users; blop is software run under your control.
- **Sub-processors:** LLM and (if used) browser automation cloud providers process prompts and may log per their terms.
- **Minimization:** Use `production_minimal` capabilities, restrict URLs, avoid recording journeys on production with real customer PII when possible (use staging).
- **Access control:** Filesystem permissions on `.blop/` and `runs/` should match your secret-store policy.

## Error payloads

Structured errors (`blop_error.code`, `details`) are intended for automation. Avoid logging full payloads in shared systems if they may embed internal URLs or user-visible strings from the target app.
