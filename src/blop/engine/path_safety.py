"""Path safety helpers for user-provided file and directory inputs."""

from __future__ import annotations

import re
from pathlib import Path

_SAFE_COMPONENT = re.compile(r"^[A-Za-z0-9._-]+$")


def sanitize_component(value: str, *, field_name: str) -> str:
    """Validate a single filename-safe component (no separators)."""
    cleaned = (value or "").strip()
    if not cleaned:
        raise ValueError(f"{field_name} is required")
    if "/" in cleaned or "\\" in cleaned:
        raise ValueError(f"{field_name} must not contain path separators")
    if not _SAFE_COMPONENT.fullmatch(cleaned):
        raise ValueError(f"{field_name} may only contain letters, numbers, '.', '_' and '-'")
    return cleaned


def resolve_within_base(
    raw_path: str,
    *,
    base_dir: Path,
    must_exist: bool = False,
    allow_absolute_outside_base: bool = False,
) -> Path | None:
    """Resolve path and ensure it remains within base_dir."""
    if not raw_path or not raw_path.strip():
        return None
    base = base_dir.resolve()
    candidate = Path(raw_path.strip()).expanduser()
    if candidate.is_absolute() and allow_absolute_outside_base:
        resolved = candidate.resolve(strict=False)
        if must_exist and not resolved.exists():
            return None
        return resolved
    if not candidate.is_absolute():
        candidate = base / candidate
    resolved = candidate.resolve(strict=False)
    try:
        resolved.relative_to(base)
    except ValueError:
        return None
    if must_exist and not resolved.exists():
        return None
    return resolved
