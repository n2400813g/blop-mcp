"""Incremental snapshot mode — send only DOM deltas to the LLM for token efficiency.

Controlled by BLOP_SNAPSHOT_MODE env var:
  - "full"        : send the complete ARIA tree every time (default, current behaviour)
  - "incremental" : diff against the previous snapshot and send only changed nodes
  - "none"        : skip snapshot entirely (fastest, least context)
"""
from __future__ import annotations

import hashlib
import json
import os
from typing import Optional


_SNAPSHOT_MODE = os.getenv("BLOP_SNAPSHOT_MODE", "full").lower()


def get_snapshot_mode() -> str:
    return os.getenv("BLOP_SNAPSHOT_MODE", "full").lower()


class SnapshotTracker:
    """Track ARIA tree state between interactions for incremental diffing."""

    _NON_DETERMINISTIC_KEYS = {
        "_debug",
        "_meta",
        "_metadata",
        "_timestamp",
        "_ts",
        "created_at",
        "debug",
        "last_seen",
        "metadata",
        "timestamp",
        "timestamps",
        "transient",
        "updated_at",
    }

    def __init__(self) -> None:
        self._previous_nodes: dict[str, dict] = {}

    @staticmethod
    def _canonicalize_for_hash(value: object) -> object:
        """Recursively normalize structures for deterministic hashing."""
        if isinstance(value, dict):
            return {
                k: SnapshotTracker._canonicalize_for_hash(v)
                for k, v in sorted(value.items())
                if k not in SnapshotTracker._NON_DETERMINISTIC_KEYS
            }
        if isinstance(value, list):
            return [SnapshotTracker._canonicalize_for_hash(v) for v in value]
        return value

    @staticmethod
    def _stable_node_id_hash(node: dict) -> str:
        """Build a stable short hash used as ID fallback when id/ref is missing."""
        canonical = SnapshotTracker._canonicalize_for_hash(node)
        serialized = json.dumps(
            canonical,
            sort_keys=True,
            separators=(",", ":"),
            default=str,
        )
        return hashlib.sha256(serialized.encode("utf-8")).hexdigest()[:12]

    @staticmethod
    def _node_discriminator_hash(node: dict) -> str:
        """Build a stable short hash from non-key fields for duplicate disambiguation."""
        discriminator_payload = {
            k: node[k]
            for k in sorted(node.keys())
            if k not in {"id", "ref", "role", "name"}
        }
        serialized = json.dumps(
            discriminator_payload,
            sort_keys=True,
            separators=(",", ":"),
            default=str,
        )
        return hashlib.sha256(serialized.encode("utf-8")).hexdigest()[:8]

    def compute_delta(self, current_nodes: list[dict]) -> list[dict]:
        """Return only new/changed nodes compared to the previous snapshot."""
        current_map: dict[str, dict] = {}
        for node in current_nodes:
            id_val = node.get("id")
            stable_id = id_val if id_val is not None else node.get("ref")
            id_part = (
                str(stable_id)
                if stable_id
                else f"hash:{self._stable_node_id_hash(node)}"
            )
            base_key = f"{node.get('role', '')}::{node.get('name', '')}::{id_part}"
            discriminator_hash = self._node_discriminator_hash(node)
            key = f"{base_key}::hash:{discriminator_hash}"
            duplicate_suffix = 1
            while key in current_map:
                key = f"{base_key}::hash:{discriminator_hash}::dup:{duplicate_suffix}"
                duplicate_suffix += 1
            current_map[key] = node

        delta: list[dict] = []
        for key, node in current_map.items():
            prev = self._previous_nodes.get(key)
            if prev is None or prev != node:
                delta.append(node)

        removed = [
            {**self._previous_nodes[k], "_removed": True}
            for k in self._previous_nodes
            if k not in current_map
        ]
        delta.extend(removed)

        self._previous_nodes = current_map
        return delta

    def reset(self) -> None:
        self._previous_nodes = {}


def format_snapshot_for_llm(
    nodes: list[dict],
    tracker: Optional[SnapshotTracker] = None,
) -> str:
    """Format an ARIA node list for LLM context, respecting snapshot mode."""
    mode = get_snapshot_mode()

    if mode == "none":
        return "[snapshot disabled]"

    if mode == "incremental" and tracker is not None:
        delta = tracker.compute_delta(nodes)
        if not delta:
            return "[no changes since last snapshot]"
        return json.dumps(delta, separators=(",", ":"))

    return json.dumps(nodes, separators=(",", ":"))
