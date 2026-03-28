"""File-based logger that never writes to stdout/stderr.

JSON-RPC (MCP transport) requires clean stdout. All output goes to
``.blop/blop.log`` by default (or the path in ``BLOP_DEBUG_LOG``).
Logging is always active — file stays quiet; stderr remains clean for MCP.
"""

from __future__ import annotations

import json
import logging
import logging.handlers
import os
import re
import threading
from datetime import datetime, timezone
from pathlib import Path

_LOG_CONFIGURED = False
_LOG_CONFIG_LOCK = threading.Lock()
_logger = logging.getLogger("blop")
_SECRET_VALUE_RE = re.compile(r"(?i)\b(api[_-]?key|token|secret|password)\s*[:=]\s*([^\s,;]+)")


def _redact_sensitive(text: str) -> str:
    if not text:
        return text
    return _SECRET_VALUE_RE.sub(lambda m: f"{m.group(1)}=<redacted>", text)


class _JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        entry: dict = {
            "ts": datetime.fromtimestamp(record.created, tz=timezone.utc).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "msg": _redact_sensitive(record.getMessage()),
        }
        if record.exc_info:
            entry["exc"] = _redact_sensitive(self.formatException(record.exc_info))
        # Merge any extra keys passed via extra={...}
        skip = set(logging.LogRecord.__dict__.keys()) | {
            "message",
            "asctime",
            "exc_text",
            "stack_info",
        }
        for key, val in record.__dict__.items():
            if key not in skip and not key.startswith("_"):
                entry[key] = _redact_sensitive(val) if isinstance(val, str) else val
        return json.dumps(entry, default=str)


def _ensure_configured() -> None:
    global _LOG_CONFIGURED
    if _LOG_CONFIGURED:
        return
    with _LOG_CONFIG_LOCK:
        if _LOG_CONFIGURED:
            return
        _LOG_CONFIGURED = True

        from blop.config import BLOP_DEBUG_LOG

        log_path = BLOP_DEBUG_LOG
        if not log_path:
            log_path = str(Path(".blop") / "blop.log")

        os.makedirs(os.path.dirname(log_path) or ".", exist_ok=True)
        handler = logging.handlers.RotatingFileHandler(
            log_path,
            maxBytes=10 * 1024 * 1024,  # 10 MB
            backupCount=3,
            encoding="utf-8",
        )
        handler.setFormatter(_JsonFormatter())
        _logger.addHandler(handler)
        _logger.setLevel(logging.DEBUG)
        # Never propagate to root (which may be disabled or write to stderr)
        _logger.propagate = False
        # Explicitly un-disable even if logging.disable(CRITICAL) was called
        _logger.disabled = False


def get_logger(name: str | None = None) -> logging.Logger:
    """Return a child logger that writes only to the blop log file."""
    _ensure_configured()
    child = _logger.getChild(name) if name else _logger
    child.disabled = False
    return child
