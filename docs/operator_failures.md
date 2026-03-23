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
