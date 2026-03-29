# Contributing

## Development setup

```bash
uv venv && source .venv/bin/activate
uv sync --extra dev
playwright install chromium
uv pip install -e .
```

## Tests

Default local run (respects `pyproject.toml` marker defaults):

```bash
uv run pytest tests/ -q
```

CI-style unit gate (no slow / integration / performance folders):

```bash
uv run pytest tests/ \
  --ignore=tests/integration \
  --ignore=tests/performance \
  -m "not slow" \
  -q
```

Opt-in suites:

- **Slow / network:** set `GOOGLE_API_KEY` and avoid `BLOP_SKIP_NETWORK_TESTS=1`, then `pytest -m slow` as appropriate.
- **Integration:** `tests/integration/` (mobile, etc.) per module docstrings.
- **Eval harness:** `pytest -m eval_harness`

## Style

- `uv run ruff check src tests` and `uv run ruff format src tests`
- Match existing patterns in `src/blop/`; see `AGENTS.md` / `CLAUDE.md` for architecture notes.

## Releases

- Update **`CHANGELOG.md`** under `[Unreleased]` → version section.
- Follow **`docs/VERSIONING.md`** for semver.
- Tag after merge: `vX.Y.Z`

## Security

See **`SECURITY.md`** for vulnerability reporting (do not file security issues as public bugs).
