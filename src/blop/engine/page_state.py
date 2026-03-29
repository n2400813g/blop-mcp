from __future__ import annotations

from dataclasses import dataclass

from blop.engine.dom_utils import extract_interactive_nodes_flat


@dataclass
class _PageStateEntry:
    snapshot: dict | None
    interactive_nodes: list[dict]
    formatted_aria: str


class PageStateCache:
    """Small per-page cache for expensive accessibility/tree extraction."""

    def __init__(self) -> None:
        self._entries: dict[tuple[int, str, bool, int], _PageStateEntry] = {}

    def invalidate(self, page=None) -> None:
        if page is None:
            self._entries.clear()
            return
        page_id = id(page)
        self._entries = {key: value for key, value in self._entries.items() if key[0] != page_id}

    def _key(self, page, interesting_only: bool, max_nodes: int) -> tuple[int, str, bool, int]:
        return (id(page), getattr(page, "url", "") or "", interesting_only, max_nodes)

    async def get_accessibility_snapshot(self, page, *, interesting_only: bool = True) -> dict | None:
        entry = self._entries.get(self._key(page, interesting_only, 0))
        if entry is not None:
            return entry.snapshot
        snapshot = await page.accessibility.snapshot(interesting_only=interesting_only)
        self._entries[self._key(page, interesting_only, 0)] = _PageStateEntry(
            snapshot=snapshot if isinstance(snapshot, dict) else None,
            interactive_nodes=[],
            formatted_aria="",
        )
        return snapshot if isinstance(snapshot, dict) else None

    async def get_interactive_nodes(
        self,
        page,
        *,
        interesting_only: bool = True,
        max_nodes: int = 40,
    ) -> list[dict]:
        key = self._key(page, interesting_only, max_nodes)
        entry = self._entries.get(key)
        if entry is not None and entry.interactive_nodes:
            return entry.interactive_nodes

        snapshot = await self.get_accessibility_snapshot(page, interesting_only=interesting_only)
        nodes = extract_interactive_nodes_flat(snapshot, max_nodes=max_nodes) if snapshot else []
        self._entries[key] = _PageStateEntry(
            snapshot=snapshot,
            interactive_nodes=nodes,
            formatted_aria=entry.formatted_aria if entry else "",
        )
        return nodes

    async def get_formatted_aria(
        self,
        page,
        *,
        interesting_only: bool = True,
        max_nodes: int = 40,
    ) -> str:
        key = self._key(page, interesting_only, max_nodes)
        entry = self._entries.get(key)
        if entry is not None and entry.formatted_aria:
            return entry.formatted_aria

        from blop.engine.snapshots import format_snapshot_for_llm

        nodes = await self.get_interactive_nodes(
            page,
            interesting_only=interesting_only,
            max_nodes=max_nodes,
        )
        formatted = format_snapshot_for_llm(nodes) if nodes else ""
        snapshot = entry.snapshot if entry else self._entries.get(key, _PageStateEntry(None, [], "")).snapshot
        self._entries[key] = _PageStateEntry(
            snapshot=snapshot,
            interactive_nodes=nodes,
            formatted_aria=formatted,
        )
        return formatted
