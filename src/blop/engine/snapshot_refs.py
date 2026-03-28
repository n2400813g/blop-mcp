"""Playwright-MCP-style accessibility snapshot with stable element refs."""

from __future__ import annotations

import json
from dataclasses import dataclass


@dataclass
class SnapshotNode:
    ref: str
    role: str
    name: str
    selector: str
    disabled: bool = False


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
