"""Shared DOM / ARIA-tree helpers used across engine modules."""

from __future__ import annotations

from typing import Optional

INTERACTIVE_ROLES = frozenset(
    {
        "button",
        "link",
        "textbox",
        "checkbox",
        "radio",
        "combobox",
        "listbox",
        "menuitem",
        "tab",
        "switch",
        "searchbox",
        "spinbutton",
    }
)


def extract_nodes_flat(
    node: dict,
    *,
    allowed_roles: frozenset[str],
    max_nodes: int = 50,
    require_name: bool = True,
    include_value: bool = False,
    include_level: bool = False,
    _count: Optional[list[int]] = None,
) -> list[dict]:
    """Flatten an ARIA snapshot into compact role/name node dicts."""
    if _count is None:
        _count = [0]

    role = node.get("role", "")
    name = node.get("name", "")
    results: list[dict] = []

    has_name = bool(name)
    if role in allowed_roles and _count[0] < max_nodes and (has_name or not require_name):
        entry: dict = {"role": role}
        if has_name:
            entry["name"] = name
        if node.get("disabled"):
            entry["disabled"] = True
        if include_value and "value" in node and node.get("value") is not None:
            entry["value"] = node["value"]
        if include_level and role == "heading" and "level" in node and node.get("level") is not None:
            entry["level"] = node["level"]
        results.append(entry)
        _count[0] += 1

    for child in node.get("children", []):
        if _count[0] >= max_nodes:
            break
        if isinstance(child, dict):
            results.extend(
                extract_nodes_flat(
                    child,
                    allowed_roles=allowed_roles,
                    max_nodes=max_nodes,
                    require_name=require_name,
                    include_value=include_value,
                    include_level=include_level,
                    _count=_count,
                )
            )
    return results


def extract_interactive_nodes_flat(
    node: dict,
    max_nodes: int = 50,
    _count: Optional[list[int]] = None,
) -> list[dict]:
    """Flatten an ARIA snapshot into compact interactive nodes.

    Shared by discovery, regression, and assertion modules.
    """
    return extract_nodes_flat(
        node,
        allowed_roles=INTERACTIVE_ROLES,
        max_nodes=max_nodes,
        require_name=True,
        include_value=False,
        include_level=False,
        _count=_count,
    )
