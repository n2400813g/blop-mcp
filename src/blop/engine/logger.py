"""File-based debug logger that never writes to stdout/stderr.

JSON-RPC (MCP transport) requires clean stdout. All debug output goes to
``.blop/debug.log`` by default (or the path in ``BLOP_DEBUG_LOG``). Debug
logging is enabled when ``BLOP_DEBUG`` is unset, and disabled only when
``BLOP_DEBUG`` is set to ``"0"``/``"false"``/``"no"``/``"off"``.
"""
from __future__ import annotations

import logging
import os
import threading
from pathlib import Path

_LOG_CONFIGURED = False
_LOG_CONFIG_LOCK = threading.Lock()
_logger = logging.getLogger("blop.debug")


def _ensure_configured() -> None:
    global _LOG_CONFIGURED
    if _LOG_CONFIGURED:
        return
    with _LOG_CONFIG_LOCK:
        if _LOG_CONFIGURED:
            return
        _LOG_CONFIGURED = True

        enabled = os.getenv("BLOP_DEBUG", "1").lower()
        if enabled in ("0", "false", "no", "off"):
            _logger.addHandler(logging.NullHandler())
            _logger.setLevel(logging.CRITICAL + 1)
            return

        log_path = os.getenv("BLOP_DEBUG_LOG", "")
        if not log_path:
            log_path = str(Path(".blop") / "debug.log")

        os.makedirs(os.path.dirname(log_path) or ".", exist_ok=True)
        handler = logging.FileHandler(log_path, encoding="utf-8")
        handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s %(message)s"))
        _logger.addHandler(handler)
        _logger.setLevel(logging.DEBUG)


def get_logger(name: str | None = None) -> logging.Logger:
    """Return a child logger that writes only to the debug log file."""
    _ensure_configured()
    if name:
        return _logger.getChild(name)
    return _logger
