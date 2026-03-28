"""Gated smoke test for scripts/benchmark_mcp_demoblaze.py (no browser by default)."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[2]
_SCRIPT = _REPO_ROOT / "scripts" / "benchmark_mcp_demoblaze.py"


@pytest.mark.integration
def test_demoblaze_benchmark_script_smoke(tmp_path, monkeypatch):
    if os.environ.get("RUN_DEMOBLAZE_BENCH") != "1":
        pytest.skip("Set RUN_DEMOBLAZE_BENCH=1 to run DemoBlaze benchmark smoke test")

    monkeypatch.setenv("BLOP_DB_PATH", str(tmp_path / "bench.db"))

    proc = subprocess.run(
        [
            sys.executable,
            str(_SCRIPT),
            "--skip-browser",
            "--iterations",
            "2",
            "--json",
        ],
        cwd=str(_REPO_ROOT),
        capture_output=True,
        text=True,
        timeout=120,
        env={**os.environ, "BLOP_DB_PATH": str(tmp_path / "bench.db")},
    )
    assert proc.returncode == 0, proc.stderr
    data = json.loads(proc.stdout)
    assert data.get("target_url")
    assert "context" in data.get("phases", {})
    assert "resource_hotpaths" in data["phases"]
    assert "release_startup" in data["phases"]
    assert "multi_flow_replay_overhead" in data["phases"]
    assert "capture_policy_comparison" in data["phases"]
    assert "targets" in data
    ctx = data["phases"]["context"]
    assert "get_journeys_for_release" in ctx
    assert ctx["get_journeys_for_release"]["n"] == 2
    assert data["phases"]["resource_hotpaths"]["journeys_resource"]["n"] == 2
    assert data["phases"]["release_startup"]["run_regression_test"]["n"] == 2
