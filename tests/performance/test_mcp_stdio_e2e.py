"""Gated stdio MCP E2E: subprocess blop-mcp + real tools/call (optional browser)."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[2]
_SCRIPT = _REPO_ROOT / "scripts" / "mcp_stdio_e2e_demoblaze.py"


@pytest.mark.integration
@pytest.mark.slow
def test_mcp_stdio_e2e_skip_browser():
    if os.environ.get("RUN_MCP_STDIO_E2E") != "1":
        pytest.skip("Set RUN_MCP_STDIO_E2E=1 to run stdio MCP E2E test")

    proc = subprocess.run(
        [
            sys.executable,
            str(_SCRIPT),
            "--skip-browser",
            "--json",
        ],
        cwd=str(_REPO_ROOT),
        capture_output=True,
        text=True,
        timeout=180,
        env=os.environ.copy(),
    )
    assert proc.returncode == 0, proc.stderr
    data = json.loads(proc.stdout)
    assert data["results"]["get_workspace_context"].get("ok") is True
    assert data["timings_s"]["initialize_s"] >= 0


@pytest.mark.integration
@pytest.mark.slow
def test_mcp_stdio_e2e_navigate_demoblaze():
    """Full Playwright chain; requires chromium + network."""
    if os.environ.get("RUN_MCP_STDIO_E2E_FULL") != "1":
        pytest.skip("Set RUN_MCP_STDIO_E2E_FULL=1 for navigate+snapshot against DemoBlaze")

    proc = subprocess.run(
        [
            sys.executable,
            str(_SCRIPT),
            "--url",
            "https://www.demoblaze.com",
            "--json",
        ],
        cwd=str(_REPO_ROOT),
        capture_output=True,
        text=True,
        timeout=300,
        env=os.environ.copy(),
    )
    assert proc.returncode == 0, proc.stderr
    data = json.loads(proc.stdout)
    assert data["results"]["navigate_to_url"]["ok"] is True
    assert "demoblaze" in data["results"]["navigate_to_url"]["data"]["url"].lower()
    assert data["results"]["get_page_snapshot"]["ok"] is True
    assert data["results"]["get_page_snapshot"]["data"]["node_count"] > 0
