"""Dual DOM context modes — optimized representations for different LLM tasks.

Action mode (default): Interactive elements only with highlight indices — minimal tokens.
Verification mode: Include up to 150 static elements (text, headings, images) for assertion evaluation.
"""
from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Literal, Optional

if TYPE_CHECKING:
    from playwright.async_api import Page

from blop.engine.dom_utils import INTERACTIVE_ROLES, extract_nodes_flat


STATIC_ROLES = frozenset({
    "heading", "img", "paragraph", "text", "cell", "row",
    "listitem", "status", "alert", "tooltip", "dialog",
    "banner", "navigation", "main", "contentinfo",
})

logger = logging.getLogger(__name__)


async def capture_dom_context(
    page: "Page",
    mode: Literal["action", "verification"] = "action",
    max_interactive: int = 50,
    max_static: int = 150,
) -> list[dict]:
    """Capture DOM context optimized for the given mode."""
    max_nodes = max_interactive if mode == "action" else max_interactive + max_static
    try:
        snapshot = await page.accessibility.snapshot(interesting_only=(mode == "action"))
        if not snapshot or not isinstance(snapshot, dict):
            # Try with interesting_only=False before falling back
            try:
                snapshot = await page.accessibility.snapshot(interesting_only=False)
            except Exception:
                snapshot = None
        if snapshot and isinstance(snapshot, dict):
            if mode == "action":
                nodes = extract_nodes_flat(
                    snapshot,
                    allowed_roles=INTERACTIVE_ROLES,
                    max_nodes=max_interactive,
                    require_name=True,
                    include_value=False,
                    include_level=False,
                )
            else:
                nodes = extract_nodes_flat(
                    snapshot,
                    allowed_roles=INTERACTIVE_ROLES | STATIC_ROLES,
                    max_nodes=max_nodes,
                    require_name=False,
                    include_value=True,
                    include_level=True,
                )
            if nodes:
                return nodes
    except Exception:
        logger.debug("Accessibility snapshot capture failed; falling back to DOM extraction.", exc_info=True)

    # DOM fallback: compute effective roles from HTML semantics
    try:
        dom_nodes = await page.evaluate("""(maxNodes) => {
            const TAG_ROLE = {a:'link',button:'button',select:'combobox',textarea:'textbox',h1:'heading',h2:'heading',h3:'heading'};
            const INPUT_ROLE = {checkbox:'checkbox',radio:'radio',button:'button',submit:'button',reset:'button'};
            const SELECTORS = ['a[href]','button','input:not([type="hidden"])','select','textarea','[role]','h1','h2','h3'];
            const seen = new Set();
            const results = [];
            for (const sel of SELECTORS) {
                if (results.length >= maxNodes) break;
                for (const el of document.querySelectorAll(sel)) {
                    if (results.length >= maxNodes) break;
                    if (seen.has(el)) continue;
                    seen.add(el);
                    const rect = el.getBoundingClientRect();
                    if (rect.width === 0 && rect.height === 0) continue;
                    const tag = el.tagName.toLowerCase();
                    const explicitRole = el.getAttribute('role');
                    let role = explicitRole;
                    if (!role) {
                        if (tag === 'input') role = INPUT_ROLE[el.type] || 'textbox';
                        else role = TAG_ROLE[tag] || null;
                    }
                    if (!role) continue;
                    const name = (
                        el.getAttribute('aria-label') ||
                        el.getAttribute('title') ||
                        (el.textContent||'').trim().slice(0,80) ||
                        el.getAttribute('placeholder') ||
                        el.value || ''
                    ).trim();
                    if (!name) continue;
                    results.push({role, name, disabled: el.disabled || el.getAttribute('disabled') !== null});
                }
            }
            return results;
        }""", max_nodes)
        return dom_nodes or []
    except Exception:
        return []
