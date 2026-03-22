# Releasing `blop-mcp`

This repo now supports a standard PyPI release flow for the Python runtime.

## Local release checklist

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

## Publish

After the smoke check passes:

```bash
python -m twine upload dist/*
```

If you want a safer dry run first, upload to TestPyPI using your preferred credentials and then repeat the same smoke check against the published package.
