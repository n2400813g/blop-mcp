"""Developer tooling helpers for blop."""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
from pathlib import Path

_BLOP_ENV_VARS = [
    "GOOGLE_API_KEY",
    "ANTHROPIC_API_KEY",
    "OPENAI_API_KEY",
    "BLOP_LLM_PROVIDER",
    "BLOP_LLM_MODEL",
    "BLOP_APP_URL",
    "APP_BASE_URL",  # legacy name read by config.py
    "LOGIN_URL",
    "TEST_USERNAME",
    "TEST_PASSWORD",
    "BLOP_DB_PATH",
    "BLOP_RUNS_DIR",
    "BLOP_DEBUG_LOG",
]


def launch_inspector() -> int:
    """Launch MCP Inspector pointed at the local blop MCP server.

    Opens an interactive UI at http://localhost:6274 for calling blop tools
    without needing a Claude/Cursor integration.

    Requires Node.js (npx) and uv to be installed.

    Flags:
      --port PORT         UI port (CLIENT_PORT, default 6274)
      --server-port PORT  Proxy port (SERVER_PORT, default 6277)
      --no-open           Disable auto-open browser
      Any other flags are passed through to the inspector.
    """
    _repo_root = Path(__file__).parent.parent.parent  # src/blop/devtools.py -> repo root

    # Load .env so env vars are available for forwarding even if not shell-exported
    try:
        from dotenv import load_dotenv

        _env_path = _repo_root / ".env"
        if _env_path.exists():
            load_dotenv(_env_path, override=False)  # shell env takes precedence
    except Exception:
        pass

    # Parse blop-specific flags; unknown flags pass through to inspector
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--port", type=int, default=None)
    parser.add_argument("--server-port", type=int, default=None)
    parser.add_argument("--no-open", action="store_true", default=False)
    args, extra_argv = parser.parse_known_args(sys.argv[1:])

    # Forward non-empty blop env vars to the MCP server process via -e flags
    server_env_flags: list[str] = []
    for key in _BLOP_ENV_VARS:
        val = os.environ.get(key, "")
        if val:
            server_env_flags += ["-e", f"{key}={val}"]

    # Inspector process environment: controls timeouts, ports, auto-open
    inspector_env = os.environ.copy()
    inspector_env["MCP_SERVER_REQUEST_TIMEOUT"] = "600000"  # 10 min per request
    inspector_env["MCP_REQUEST_MAX_TOTAL_TIMEOUT"] = "600000"  # 10 min cumulative
    if args.port is not None:
        inspector_env["CLIENT_PORT"] = str(args.port)
    if args.server_port is not None:
        inspector_env["SERVER_PORT"] = str(args.server_port)
    if args.no_open:
        inspector_env["MCP_AUTO_OPEN_ENABLED"] = "false"

    client_port = inspector_env.get("CLIENT_PORT", "6274")

    # Point at local dev version via uv --directory (not the published PyPI package)
    server_cmd = ["uv", "--directory", str(_repo_root), "run", "blop-mcp"]
    cmd = ["npx", "@modelcontextprotocol/inspector"] + server_env_flags + extra_argv + server_cmd

    print(f"Launching MCP Inspector — open http://localhost:{client_port} in your browser")
    print(f"Command: {' '.join(cmd)}")
    try:
        result = subprocess.run(cmd, check=False, env=inspector_env)
        return result.returncode
    except FileNotFoundError:
        print("npx not found. Install Node.js to use blop-inspect.", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(launch_inspector())
