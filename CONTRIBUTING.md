# Contributing to blop

Thanks for helping improve blop. This project aims to stay easy to ship and safe for open-source use.

## Setup

```bash
uv venv && source .venv/bin/activate   # optional if you prefer an activated shell
uv sync --extra dev
playwright install chromium
```

Use **`uv run …`** for CLI tools installed into the project (pre-commit, ruff, pytest). They live in `.venv` and are not on your global `PATH` unless the venv is activated.

Copy `.env.example` to `.env` and add your LLM key (see README). Never commit `.env`.

For Cursor, copy `.cursor/mcp.json.example` to `.cursor/mcp.json` and use absolute paths plus the same env values. The real `mcp.json` is gitignored so API keys do not land in git.

## Do not commit

- `.env` or any file with real API keys or production credentials
- `.cursor/mcp.json` (local only)
- `.blop/`, `runs/`, `auth_state*.json`, SQLite files from local runs
- Cursor plan scratch under `.cursor/plans/`
- Build caches: `.venv/`, `.uv-cache/`, `__pycache__/`, `.pytest_cache/`, etc.

Prefer `git add -p` or explicit paths instead of `git add .` when your working tree mixes unrelated changes.

## Before you open a PR

1. Install Git hooks once: `uv run pre-commit install`  
   (The hook runs `python -m pre_commit` from this repo’s `.venv`, so you do **not** need a global `pre-commit` on `PATH`.)
2. Run checks: `uv run pre-commit run --all-files` (or commit as usual and let the hook run)
3. Run tests: `uv run pytest`
4. Optional: `uv run ruff check src tests` and `uv run ruff format --check src tests`

CI runs Ruff and the packaging smoke tests; keeping hooks green avoids review churn.

### Troubleshooting

| Symptom | What to do |
|--------|------------|
| `pre-commit: command not found` | Use `uv run pre-commit …`, or activate the venv (`source .venv/bin/activate`) and run `pre-commit` again. |
| Many pytest **errors** / import failures in ~2s | Usually wrong interpreter: run **`uv run pytest`** from the repo root after `uv sync --extra dev`, not system `pytest`. |
| `No module named pytest` / `blop` | Same: use `uv run pytest` so the project environment is used. |

## Where to discuss

- Bugs and features: [Issue Tracker](https://github.com/n2400813g/blop-mcp/issues)
- Larger changes: open an issue first so direction aligns with maintainers

## Code style

Ruff is configured in `pyproject.toml` (`[tool.ruff]`). Match surrounding patterns in files you touch; avoid drive-by refactors unrelated to your change.
