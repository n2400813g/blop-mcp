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

Use these release gates before shipping:

- `install_or_upgrade_failure` in smoke coverage is a release blocker.
- `auth_session_failure` in any release-gating journey is a release blocker.
- `unknown_unclassified` in release smoke is a release blocker unless explicitly waived.
- `stale_flow_drift` and `selector_healing_failure` mean replay trust is reduced; refresh or re-record before trusting a green release decision.

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
