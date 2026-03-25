# Operator Guide For Common Production Failures

## Canonical Stability Buckets

| Bucket | Recognize It By | First Place To Look | First Remediation | Escalate When |
|---|---|---|---|---|
| `auth_session_failure` | `waiting_auth`, expired session, missing or unresolved auth profile | `auth_provenance`, `validate_release_setup(...)`, auth health events | Refresh or re-capture auth, then validate again | release-gating journeys still cannot authenticate |
| `stale_flow_drift` | stale recording guidance, old flow age, replay no longer matches current UI | `stale_flow_guidance`, flow recording age, screenshots/trace | Re-record the journey and rerun replay | refreshed flow still fails |
| `selector_healing_failure` | repair rejected, low-confidence healing, replay trust review required | `replay_trust_summary`, failed step evidence, repair confidence | Refresh the selector path or re-record the flow | repaired flow still needs risky healing |
| `environment_runtime_misconfig` | DB, runtime, config, path-policy, browser-launch, or local setup issues | `validate_release_setup(...)`, runtime posture, host config | Fix the local/runtime precondition and rerun validation | runtime remains degraded after fix |
| `install_or_upgrade_failure` | install smoke, Chromium/runtime setup, entrypoint startup, upgrade/reinstall failures | clean-environment smoke output, package install logs | Repair the install or runtime packaging issue first | a clean install or upgrade still fails |
| `network_transient_infra` | app unreachable, timeouts, DNS, 502/503/504, transient upstream failures | reachability checks, network logs, app health | Restore app/network health and retry | repeat failures continue after infra recovery |
| `product_regression` | evidence points to a real app behavior regression | trace, screenshots, console/network evidence | Fix the product bug and rerun release validation | regression affects release-gating journeys |
| `unknown_unclassified` | report says evidence is insufficient to classify confidently | `unknown_classification_gaps`, missing trace/screenshots/errors | capture the missing evidence and rerun | unknown persists in release smoke or release-gating flows |

## Per-bucket quick reference

### `auth_session_failure`
**Tool to run:** `validate_release_setup(app_url=..., profile_name=...)`
**Look at:** `auth_provenance.session_validation_status` in run results; health events of type `auth_context_resolved`
**Fix:** `capture_auth_session(app_url=..., profile_name=...)` → `validate_release_setup(...)` → retry replay
**Escalate when:** release-gating journeys still cannot authenticate after re-capture

### `stale_flow_drift`
**Tool to run:** `get_test_results(run_id=...)` → inspect `stale_flow_guidance` and `flow_staleness`
**Look at:** `replay_trust_summary.stale_case_count`; flow recording age in `flow_recorded_at`
**Fix:** `record_test_flow(app_url=..., goal=...)` to refresh the recording; re-run `run_release_check(...)`
**Escalate when:** refreshed flow still fails — may indicate a real product regression

### `selector_healing_failure`
**Tool to run:** `get_test_results(run_id=...)` → inspect `replay_trust_summary` and `failure_classification`
**Look at:** `healing_decision`, `repair_rejected` in `failure_reason_codes`; screenshots in `runs/screenshots/{run_id}/`
**Fix:** Re-record the affected flow; if risky healing keeps being proposed, treat as a UI change
**Escalate when:** re-recorded flow still requires risky healing or repair is rejected repeatedly

### `environment_runtime_misconfig`
**Tool to run:** `validate_setup(app_url=...)` — look for `blocked` or `warnings` status
**Look at:** `bucketed_blockers` in setup output; `BLOP_DB_PATH`, `BLOP_RUNS_DIR`, `BLOP_DEBUG_LOG` env vars
**Fix:** Correct the misconfigured path or env var; re-run `validate_setup(...)` until status is `ready`
**Escalate when:** runtime remains degraded after fixing the reported precondition

### `install_or_upgrade_failure`
**Tool to run:** `validate_setup(app_url=...)` → check `chromium_installed` check result
**Look at:** `install_or_upgrade_failure` bucket in `validate_release_setup(...)` output; clean-environment smoke logs
**Fix:** `playwright install chromium` or repair the package install; repeat smoke path
**Escalate when:** clean install or upgrade still fails after running the installer

### `network_transient_infra`
**Tool to run:** `validate_setup(app_url=...)` → `app_url_reachable` check; network logs at `runs/network/{run_id}/`
**Look at:** `network_errors` in run cases; 502/503/504 in console/network logs
**Fix:** Restore app or network health; retry after upstream recovery
**Escalate when:** repeated failures continue after infra recovery — may be a persistent regression

### `product_regression`
**Tool to run:** `debug_test_case(case_id=..., app_url=...)` for verbose headed evidence
**Look at:** screenshots at `runs/screenshots/{run_id}/{case_id}/`; `console_errors`, `assertion_failures` in case
**Fix:** Fix the underlying product behavior; re-run `run_release_check(...)` to confirm regression is gone
**Escalate when:** regression affects any revenue or activation journey

### `unknown_unclassified`
**Tool to run:** `get_test_results(run_id=...)` → inspect `unknown_classification_gaps` and `unknown_next_observation`
**Look at:** `trace_path`, `artifact_paths`, `failure_reason_codes` — determine what evidence is missing
**Fix:** Re-run with `debug_test_case(...)` to capture fuller evidence; classify once signals are present
**Escalate when:** unknown persists across multiple re-runs in release smoke or release-gating flows

---

## `blop://health` shows `degraded`

- Check whether the LLM key is present
- Check DB reachability
- Confirm Chromium is installed on the host
- Review runtime posture warnings for path-policy or host-policy issues

## `validate_release_setup(...)` returns `blocked`

- Fix the top blocker first
- Re-run `validate_release_setup(...)`
- Do not trust release-gating results until the status is no longer blocked

## Runs return `waiting_auth`

- Refresh auth with `capture_auth_session(...)`
- Re-run `validate_release_setup(app_url=..., profile_name=...)`
- Retry replay only after validation is clean
- Treat this as `auth_session_failure` for release gating

## Replay failures look like drift

- Inspect `replay_trust_summary`, `failure_classification`, and `stale_flow_guidance`
- Refresh the journey with `record_test_flow(...)`
- Re-run `run_release_check(..., mode="replay")`
- If replay reports `stale_flow_drift` or `selector_healing_failure`, refresh or re-record before trusting a green result

## Artifact or log paths look wrong

- Confirm `BLOP_DB_PATH`, `BLOP_RUNS_DIR`, and `BLOP_DEBUG_LOG`
- In production-style setups, keep all three as absolute paths
- Re-check `blop://health` and `validate_release_setup(...)`

## Release Stability Gates

- Block release on any `install_or_upgrade_failure` from smoke coverage
- Block release on any `auth_session_failure` in release-gating journeys
- Block release on any `unknown_unclassified` result in release smoke unless explicitly waived
- Treat `stale_flow_drift` and `selector_healing_failure` as replay-trust failures that must be refreshed before trusting ship/no-ship output
- Use `get_risk_analytics()` to review `top_bucket_counts`, `most_common_blocker_buckets`, and `highest_pain_buckets` before deciding which instability class to fix first
