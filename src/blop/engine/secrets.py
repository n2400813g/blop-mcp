"""Secrets masking — redact credentials from LLM context to prevent leakage.

Reads secret values from .blop/secrets.env (or BLOP_SECRETS_FILE env var).
Before sending any page content, assertion text, or screenshots to the LLM,
secret values are replaced with [REDACTED].
"""
from __future__ import annotations

import os
import re
from pathlib import Path

_secrets_cache: list[str] | None = None


def _load_secrets() -> list[str]:
    """Load secret values from the secrets file."""
    global _secrets_cache
    if _secrets_cache is not None:
        return _secrets_cache

    secrets_file = os.getenv("BLOP_SECRETS_FILE", "")
    if not secrets_file:
        repo_root = Path(__file__).parent.parent.parent
        secrets_file = str(repo_root / ".blop" / "secrets.env")

    secrets: list[str] = []
    p = Path(secrets_file)
    if p.exists():
        for line in p.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            if "=" in line:
                _, _, value = line.partition("=")
                value = value.strip().strip("'\"")
                if value and len(value) >= 3:
                    secrets.append(value)

    # Redact longer overlapping secrets first to avoid partial masking.
    secrets = sorted(secrets, key=len, reverse=True)
    _secrets_cache = secrets
    return secrets


def reload_secrets() -> int:
    """Force-reload secrets from disk. Returns count of secrets loaded."""
    global _secrets_cache
    _secrets_cache = None
    return len(_load_secrets())


def mask_text(text: str) -> str:
    """Replace any secret values found in text with [REDACTED]."""
    secrets = _load_secrets()
    if not secrets:
        return text

    masked = text
    for secret in secrets:
        if secret in masked:
            masked = masked.replace(secret, "[REDACTED]")
    return masked


def mask_dict(data: dict) -> dict:
    """Recursively mask secret values in a dictionary."""
    def _mask_any(value):
        if isinstance(value, str):
            return mask_text(value)
        if isinstance(value, dict):
            return mask_dict(value)
        if isinstance(value, list):
            return [_mask_any(v) for v in value]
        return value

    masked = {}
    for key, value in data.items():
        masked[key] = _mask_any(value)
    return masked


def has_secrets() -> bool:
    """Return True if any secrets are configured."""
    return len(_load_secrets()) > 0
