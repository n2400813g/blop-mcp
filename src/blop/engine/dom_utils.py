"""Shared DOM / ARIA-tree helpers used across engine modules."""
from __future__ import annotations

from typing import Optional

INTERACTIVE_ROLES = frozenset({
    "button", "link", "textbox", "checkbox", "radio", "combobox",
    "listbox", "menuitem", "tab", "switch", "searchbox", "spinbutton",
})


def extract_interactive_nodes_flat(
    node: dict,
    max_nodes: int = 50,
    _count: Optional[list[int]] = None,
) -> list[dict]:
    """Flatten an ARIA snapshot into compact interactive nodes.

    Shared by discovery, regression, and assertion modules.
    """
    if _count is None:
        _count = [0]
    role = node.get("role", "")
    name = node.get("name", "")
    results: list[dict] = []
    if role in INTERACTIVE_ROLES and name and _count[0] < max_nodes:
        entry: dict = {"role": role, "name": name}
        if node.get("disabled"):
            entry["disabled"] = True
        results.append(entry)
        _count[0] += 1

    for child in node.get("children", []):
        if _count[0] >= max_nodes:
            break
        if isinstance(child, dict):
            results.extend(extract_interactive_nodes_flat(child, max_nodes=max_nodes, _count=_count))
    return results
