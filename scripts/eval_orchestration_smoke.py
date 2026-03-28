#!/usr/bin/env python3
"""Offline orchestration smoke (BrowserGym-style eval hook). Run from repo root:

    python scripts/eval_orchestration_smoke.py

Or: pytest tests/eval_harness/ -m eval_harness
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def main() -> int:
    cmd = [
        sys.executable,
        "-m",
        "pytest",
        str(ROOT / "tests" / "eval_harness"),
        "-m",
        "eval_harness",
        "-v",
    ]
    return subprocess.call(cmd)


if __name__ == "__main__":
    raise SystemExit(main())
