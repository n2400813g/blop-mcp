"""Playwright-MCP-style accessibility snapshot with stable element refs."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass


@dataclass
class SnapshotNode:
    ref: str
    role: str
    name: str
    selector: str
    disabled: bool = False
    stable_key: str = ""


def build_stable_key(*, role: str, name: str, selector: str, disabled: bool = False) -> str:
    """Build a short deterministic key for a snapshot node."""
    payload = json.dumps(
        {
            "role": role,
            "name": name,
            "selector": selector,
            "disabled": disabled,
        },
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha1(payload.encode("utf-8")).hexdigest()[:12]


def render_snapshot_markdown(nodes: list[SnapshotNode]) -> str:
    """Render compact markdown snapshot expected by browser_* tools."""
    if not nodes:
        return "- page: no interactive elements found"

    lines = ["- page:"]
    for node in nodes:
        suffix = " [disabled]" if node.disabled else ""
        escaped_name = json.dumps(node.name)[1:-1]
        lines.append(f'  - {node.role} "{escaped_name}" [ref={node.ref}]{suffix}')
    return "\n".join(lines)
