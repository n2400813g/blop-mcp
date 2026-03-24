# Releasing `blop-mcp`

This repo now supports a standard PyPI release flow for the Python runtime.

## Package release checklist

Before cutting a release:

- Confirm the version in `pyproject.toml` is the one you intend to publish.
- Make sure the changelog or release notes for the user-facing changes are ready.
- Verify the working tree contains only the intended release changes.
- Confirm the target package name on PyPI is `blop-mcp`.
- Review the latest stability bucket summary from `get_risk_analytics()` and confirm `unknown_unclassified` is not trending upward.
- Check `get_risk_analytics()` for `most_common_blocker_buckets` and `highest_pain_buckets` so the release decision reflects the current top instability classes, not just raw failure count.

## Stability exit criteria

Use this checklist before shipping. All five items must be satisfied or explicitly waived.

### 1. Stability gate status — no blocking buckets

Run `get_risk_analytics()` and confirm:
- `stability_buckets` contains no `install_or_upgrade_failure` entries (or the count is zero).
- `stability_buckets` contains no `unknown_unclassified` entries, **or** `unknown_unclassified_rate` is below `0.20` (20 % of total failures).
- `auth_session_failure` is not present in release-gating journeys.

A `stability_gate_summary.release_blocked_by_stability = true` result from any `run_release_check(...)` is a hard block.

### 2. Smoke scenario status — all scenarios green

The following test scenarios must pass (no live browser required):

```bash
uv run pytest tests/test_stability_validation.py -v
# Covers: stale_flow_drift, auth_session_failure, selector_healing_failure,
#          environment_runtime_misconfig, install_or_upgrade_failure, network_transient_infra
#          release gate summary blocking logic

uv run pytest tests/test_release_policy.py -v
# Covers: ReleasePolicy gate evaluation, SHIP/INVESTIGATE/BLOCK decisions,
#          stability bucket integration, SQLite policy persistence

uv run pytest tests/test_sqlite_migrations.py -v
# Covers: migration idempotency, fresh install entrypoint resolution,
#          npm wizard shebang, non-duplicate migration error propagation
```

### 3. Policy gate status — no BLOCK from release policy

For apps with recorded flows, `run_release_check(app_url=...)` must return `decision` of `SHIP` or `INVESTIGATE`, not `BLOCK`, for revenue and activation flows. A `BLOCK` decision must be resolved before shipping.

### 4. Unknown failure ratio — below 20 %

`get_risk_analytics()` must return `unknown_unclassified_rate` ≤ `0.20`. If the ratio is above this threshold, inspect `unknown_classification_gaps` on the affected cases and re-run after adding missing evidence (trace, screenshots, failure_reason_codes).

### 5. Auth session validity

For any auth-dependent flows in the release scope, `validate_setup(app_url=..., profile_name=...)` must return `status: ready` (not `blocked`). A `waiting_auth` or `auth_session_failure` on a release-gating journey is a hard block.

### Non-blocking review items

These do not block release but must be logged and actioned in the next cycle:
- `stale_flow_drift` — refresh the recording before trusting the green result.
- `selector_healing_failure` — refresh or re-record before trusting replay output.

## Local build and smoke check

Run these commands from the repo root:

```bash
uv pip install -e ".[dev]"
rm -rf dist build
python -m build
python -m venv /tmp/blop-dist-smoke
source /tmp/blop-dist-smoke/bin/activate
pip install dist/*.whl
blop --help
blop-mcp --help
```

Expected outcome:

- `dist/` contains both an sdist and a wheel
- the wheel installs into a clean virtualenv
- `blop --help` and `blop-mcp --help` both start successfully

After the package smoke check, record the outcome in the internal stability telemetry using the canonical bucket names:

- healthy install/startup: no blocking bucket
- browser/runtime packaging failure: `install_or_upgrade_failure`
- local config or path breakage: `environment_runtime_misconfig`
- transient network/package registry failure: `network_transient_infra`

## Publish to PyPI

Use a scoped PyPI token with `__token__` as the username:

```bash
export TWINE_USERNAME="__token__"
export TWINE_PASSWORD="pypi-..."
python -m twine upload dist/*
```

Expected outcome:

- the upload succeeds without filename conflicts
- [pypi.org/project/blop-mcp](https://pypi.org/project/blop-mcp/) shows the new version
- `pip install blop-mcp==<version>` resolves from PyPI

## Post-publish verification

- Install the published version into a clean virtualenv and run `blop --help` plus `blop-mcp --help`.
- Open the PyPI project page and confirm the release metadata, README rendering, and files list look correct.
- If the token was shared, exposed, or used outside your normal secret flow, rotate it after the release.
- If post-publish smoke returns `install_or_upgrade_failure` or `unknown_unclassified`, stop rollout until the packaged path is green again.

If you want a safer dry run first, upload to TestPyPI using your preferred credentials and then repeat the same smoke check against the published package.
