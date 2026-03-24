"""Inventory-first discovery: BFS crawl → Gemini planning → quality gate."""
from __future__ import annotations

import ast
import asyncio
import json
import os
import re
import time
from dataclasses import dataclass
from typing import Any, Optional
from urllib.parse import urljoin, urlparse

from blop.config import (
    BLOP_DISCOVERY_CONCURRENCY,
    BLOP_DISCOVERY_MAX_PAGES,
    BLOP_SPA_SETTLE_MS,
)
from blop.engine.auth import resolve_storage_state_for_profile
from blop.engine.interaction import wait_for_spa_ready
from blop.engine.logger import get_logger
from blop.engine.secrets import mask_text
from blop.schemas import SiteInventory

_log = get_logger("discovery")


@dataclass(frozen=True)
class _CrawlWorkItem:
    url: str
    depth: int
    priority: float
    area_key: str
    source_url: str


@dataclass
class _CrawlPageResult:
    url: str
    depth: int
    area_key: str
    buttons: list[dict]
    links: list[dict]
    forms: list[dict]
    headings: list[str]
    routes: list[str]
    nodes: list[dict]
    auth_signals: list[str]
    business_signals: list[str]
    discovered_urls: list[str]
    error: str | None = None


def _url_priority(url: str, business_signals: list[str], auth_signals: list[str], hotspot_paths: set[str]) -> float:
    """Higher score means crawl sooner."""
    u = url.lower()
    score = 0.2
    if any(sig in u for sig in ("pricing", "checkout", "billing", "payment", "plans", "subscribe")):
        score += 0.45
    if any(sig in u for sig in ("login", "signup", "register", "auth", "dashboard")):
        score += 0.25
    if any(sig.strip("/") in u for sig in business_signals):
        score += 0.2
    if any(sig.strip("/") in u for sig in auth_signals):
        score += 0.1
    path = urlparse(url).path or "/"
    if path in hotspot_paths:
        score += 0.35
    return score


def _adaptive_budget(base_max_pages: int, business_signals: list[str], auth_signals: list[str]) -> int:
    """Increase crawl budget for richer app signals, keep bounded."""
    signal_count = len(set(business_signals + auth_signals))
    if signal_count >= 8:
        return min(40, base_max_pages + 12)
    if signal_count >= 4:
        return min(30, base_max_pages + 6)
    return base_max_pages


def _route_area_key(value: str) -> str:
    parsed = urlparse(value)
    path = parsed.path or "/"
    segments = [segment for segment in path.split("/") if segment]
    return segments[0].lower() if segments else "/"


def _dedupe_keep_order(items: list[str]) -> list[str]:
    return list(dict.fromkeys(item for item in items if item))


def _normalize_inventory_buttons(items: list[dict]) -> list[dict]:
    deduped = _dedupe_dict_items(items, ("text", "href"))
    return sorted(
        deduped,
        key=lambda item: (
            str(item.get("text", "")).strip().lower(),
            str(item.get("href", "")).strip().lower(),
            str(item.get("id", "")).strip().lower(),
        ),
    )


def _normalize_inventory_links(items: list[dict]) -> list[dict]:
    deduped = _dedupe_dict_items(items, ("source_route", "href", "text"))
    return sorted(
        deduped,
        key=lambda item: (
            str(item.get("source_route", "")).strip().lower(),
            str(item.get("href", "")).strip().lower(),
            str(item.get("text", "")).strip().lower(),
        ),
    )


def _normalize_inventory_forms(items: list[dict]) -> list[dict]:
    def _form_key(item: dict) -> tuple[str, ...]:
        inputs = item.get("inputs") or []
        input_key = ",".join(
            f"{inp.get('type', '')}:{inp.get('name', '')}:{inp.get('placeholder', '')}:{inp.get('label', '')}"
            for inp in inputs
        )
        return (
            str(item.get("action", "")).strip().lower(),
            input_key.lower(),
        )

    deduped: list[dict] = []
    seen: set[tuple[str, ...]] = set()
    for item in items:
        key = _form_key(item)
        if key in seen:
            continue
        seen.add(key)
        deduped.append(item)
    return sorted(deduped, key=_form_key)


def _resolve_discovery_worker_count(max_pages: int, *, storage_state: str | None) -> int:
    if BLOP_DISCOVERY_CONCURRENCY > 0:
        return max(1, min(BLOP_DISCOVERY_CONCURRENCY, max_pages))
    auto_default = 2 if storage_state else 3
    return max(1, min(auto_default, max_pages))


def _choose_next_work_item(frontier: list[_CrawlWorkItem], area_page_counts: dict[str, int]) -> _CrawlWorkItem:
    unexplored = [item for item in frontier if area_page_counts.get(item.area_key, 0) == 0]
    candidates = unexplored or frontier
    selected = min(
        candidates,
        key=lambda item: (
            area_page_counts.get(item.area_key, 0) if not unexplored else 0,
            -item.priority,
            item.depth,
            item.area_key,
            item.url,
        ),
    )
    frontier.remove(selected)
    return selected


async def _create_crawl_context(browser, *, storage_state: str | None):
    ctx_kwargs: dict = {}
    if storage_state:
        ctx_kwargs["storage_state"] = storage_state
    return await browser.new_context(**ctx_kwargs)


async def _extract_page_inventory(page, url: str) -> _CrawlPageResult:
    page_buttons = await page.evaluate(
        """() => Array.from(document.querySelectorAll('button, [role="button"], a.btn, .cta, [class*="btn"]'))
            .map(el => ({text: el.textContent.trim().slice(0,120), id: el.id, href: el.getAttribute('href') || null}))
            .filter(el => el.text).slice(0,25)"""
    )
    page_links = await page.evaluate(
        """() => Array.from(document.querySelectorAll('a[href]'))
            .map(el => ({text: el.textContent.trim().slice(0,120), href: el.href}))
            .filter(el => el.text && !el.href.startsWith('mailto:') && !el.href.startsWith('tel:'))
            .slice(0,35)"""
    )
    page_links = _dedupe_dict_items(page_links or [], ("href", "text"))
    page_forms = await page.evaluate(
        """() => Array.from(document.querySelectorAll('form'))
            .map(form => ({
                action: form.action,
                inputs: Array.from(form.querySelectorAll('input, textarea, select'))
                    .map(el => ({type: el.type, name: el.name, placeholder: el.placeholder, label: el.getAttribute('aria-label') || ''}))
            })).slice(0,6)"""
    )
    page_headings = await page.evaluate(
        """() => Array.from(document.querySelectorAll('h1, h2, h3'))
            .map(el => el.textContent.trim().slice(0,100))
            .filter(t => t).slice(0,10)"""
    )
    page_routes = await page.evaluate(
        """() => [...new Set(Array.from(document.querySelectorAll('a[href]'))
            .map(el => { try { return new URL(el.href).pathname; } catch(e) { return null; } })
            .filter(p => p && p !== '/'))].slice(0,30)"""
    )
    page_buttons = _dedupe_dict_items(page_buttons or [], ("text", "href"))
    page_nodes = await _capture_page_structure(page, max_nodes=50)

    source_path = urlparse(url).path or "/"
    for link in page_links:
        link["source_route"] = source_path
        link["source_url"] = url

    page_text_lower = " ".join(
        [button.get("text", "") for button in page_buttons]
        + [link.get("text", "") for link in page_links]
        + page_headings
    ).lower()
    auth_signals: list[str] = []
    business_signals: list[str] = []
    for signal in (
        "sign in", "login", "log in", "sign up", "register", "logout",
        "dashboard", "/auth", "/login", "/signup", "get started", "create account",
    ):
        if signal in page_text_lower and signal not in auth_signals:
            auth_signals.append(signal)
    for signal in (
        "pricing", "contact", "integration", "oauth", "checkout",
        "payment", "subscribe", "onboarding", "demo", "trial", "plans",
    ):
        if signal in page_text_lower and signal not in business_signals:
            business_signals.append(signal)

    routes_text = " ".join(page_routes).lower()
    for signal in ("/pricing", "/contact", "/login", "/signup", "/auth", "/checkout", "/demo"):
        if signal not in routes_text or signal in business_signals + auth_signals:
            continue
        if signal in ("/login", "/signup", "/auth"):
            auth_signals.append(signal)
        else:
            business_signals.append(signal)

    discovered_urls = [
        link.get("href", "")
        for link in page_links
        if isinstance(link.get("href"), str) and link.get("href", "").startswith("http")
    ]

    return _CrawlPageResult(
        url=url,
        depth=0,
        area_key=_route_area_key(url),
        buttons=page_buttons,
        links=page_links,
        forms=page_forms or [],
        headings=page_headings or [],
        routes=page_routes or [],
        nodes=page_nodes,
        auth_signals=auth_signals,
        business_signals=business_signals,
        discovered_urls=discovered_urls,
    )


async def _crawl_one_page(page, item: _CrawlWorkItem, *, worker_slot: int = 0) -> _CrawlPageResult:
    try:
        try:
            await page.goto(item.url, wait_until="domcontentloaded", timeout=15000)
            await wait_for_spa_ready(
                page,
                settle_ms=max(BLOP_SPA_SETTLE_MS, 750),
                timeout_ms=15000,
            )
        except Exception as first_error:
            try:
                await page.goto(item.url, timeout=15000)
                await wait_for_spa_ready(
                    page,
                    settle_ms=max(BLOP_SPA_SETTLE_MS, 750),
                    timeout_ms=15000,
                )
            except Exception as second_error:
                _log.debug(
                    "crawl_page_failed event=crawl_page_failed url=%s worker_slot=%s error_type=%s error_message=%s fallback_error_type=%s fallback_error_message=%s",
                    item.url,
                    worker_slot,
                    type(first_error).__name__,
                    str(first_error)[:160],
                    type(second_error).__name__,
                    str(second_error)[:160],
                )
                return _CrawlPageResult(
                    url=item.url,
                    depth=item.depth,
                    area_key=item.area_key,
                    buttons=[],
                    links=[],
                    forms=[],
                    headings=[],
                    routes=[],
                    nodes=[],
                    auth_signals=[],
                    business_signals=[],
                    discovered_urls=[],
                    error=f"{type(second_error).__name__}: {str(second_error)[:200]}",
                )

        result = await _extract_page_inventory(page, item.url)
        result.depth = item.depth
        result.area_key = item.area_key
        return result
    except Exception as exc:
        _log.debug(
            "crawl_page_failed event=crawl_page_failed url=%s worker_slot=%s error_type=%s error_message=%s",
            item.url,
            worker_slot,
            type(exc).__name__,
            str(exc)[:200],
            exc_info=True,
        )
        return _CrawlPageResult(
            url=item.url,
            depth=item.depth,
            area_key=item.area_key,
            buttons=[],
            links=[],
            forms=[],
            headings=[],
            routes=[],
            nodes=[],
            auth_signals=[],
            business_signals=[],
            discovered_urls=[],
            error=f"{type(exc).__name__}: {str(exc)[:200]}",
        )


def _dedupe_dict_items(items: list[dict], key_fields: tuple[str, ...]) -> list[dict]:
    seen: set[tuple[str, ...]] = set()
    deduped: list[dict] = []
    for item in items:
        key = tuple(str(item.get(field, "")).strip() for field in key_fields)
        if not any(key):
            continue
        if key in seen:
            continue
        seen.add(key)
        deduped.append(item)
    return deduped


def _parse_flow_list(text: str) -> list[dict] | None:
    cleaned = re.sub(r"```(?:json)?\s*", "", text).strip()
    match = re.search(r"\[.*\]", cleaned, re.DOTALL)
    if not match:
        return None

    candidate = match.group().strip()
    for loader in (json.loads, ast.literal_eval):
        try:
            payload = loader(candidate)
        except Exception:
            continue
        if isinstance(payload, list):
            return payload
    return None


def _heuristic_flows_from_inventory(inventory: SiteInventory) -> list[dict]:
    def _clean(text: str) -> str:
        return re.sub(r"\s+", " ", text or "").strip()

    signals: list[str] = []
    for button in inventory.buttons:
        text = _clean(button.get("text", ""))
        if text:
            signals.append(text)
    for link in inventory.links:
        text = _clean(link.get("text", ""))
        if text:
            signals.append(text)
    for heading in inventory.headings:
        text = _clean(heading)
        if text:
            signals.append(text)

    unique_signals = list(dict.fromkeys(signals))
    flows: list[dict] = []

    def _append_once(flow_name: str, goal: str, assertions: list[str], *, criticality: str = "other", severity: str = "medium", confidence: float = 0.62) -> None:
        if any(existing.get("flow_name") == flow_name for existing in flows):
            return
        flows.append(
            {
                "flow_name": flow_name,
                "goal": goal,
                "starting_url": inventory.app_url,
                "preconditions": [],
                "likely_assertions": assertions,
                "severity_if_broken": severity,
                "confidence": confidence,
                "business_criticality": criticality,
            }
        )

    for text in unique_signals:
        lower = text.lower()
        if "template" in lower:
            _append_once(
                "browse_templates",
                f"Open the template library in {inventory.app_url} and confirm ready-made starting points are available.",
                ["template library visible", "template cards or template CTA visible"],
                criticality="activation",
                severity="high",
                confidence=0.74,
            )
        if "shared" in lower:
            _append_once(
                "review_shared_content",
                f"Open the shared-content area in {inventory.app_url} and confirm shared projects or shared items can be reviewed.",
                ["shared workspace visible", "shared project list or empty state visible"],
                criticality="retention",
                severity="medium",
                confidence=0.71,
            )
        if "caption" in lower:
            _append_once(
                "start_caption_workflow",
                f"Enter the captioning workflow in {inventory.app_url} and confirm the app exposes a caption-generation entry point.",
                ["caption CTA visible", "caption workflow or upgrade gate visible"],
                criticality="activation",
                severity="high",
                confidence=0.72,
            )
        if "agent" in lower:
            _append_once(
                "enter_ai_agent",
                f"Open the AI-assisted creation flow in {inventory.app_url} and confirm the AI entry point responds.",
                ["AI agent CTA visible", "AI workflow or upgrade gate visible"],
                criticality="activation",
                severity="high",
                confidence=0.73,
            )
        if "blank project" in lower or ("project" in lower and "create" in lower):
            _append_once(
                "create_blank_project",
                f"Start a blank-project workflow in {inventory.app_url} and confirm the editor shell or creation gate appears.",
                ["project creation CTA visible", "editor shell or upgrade gate visible"],
                criticality="activation",
                severity="high",
                confidence=0.72,
            )
        if "upload" in lower or "import" in lower:
            _append_once(
                "import_media",
                f"Start a media import workflow in {inventory.app_url} and confirm upload or import controls are reachable.",
                ["upload control visible", "import step or upgrade gate visible"],
                criticality="activation",
                severity="high",
                confidence=0.69,
            )

    if inventory.routes and not any(flow.get("flow_name") == "navigate_core_routes" for flow in flows):
        _append_once(
            "navigate_core_routes",
            f"Navigate the authenticated routes exposed by {inventory.app_url} and confirm each route loads without redirect loops.",
            ["core routes reachable", "authenticated navigation remains stable"],
            criticality="other",
            severity="medium",
            confidence=0.64,
        )

    return flows[:8]


def _slugify_route_label(value: str) -> str:
    cleaned = re.sub(r"[^a-z0-9]+", "_", (value or "").lower()).strip("_")
    return cleaned or "section"


def _titleize_route_label(value: str) -> str:
    return " ".join(part.capitalize() for part in re.split(r"[_\-/]+", value or "") if part) or "Section"


def _inventory_section_fallback_flows(inventory: SiteInventory) -> list[dict]:
    """Generate public-site fallback flows that spread across distinct route sections."""
    section_map: dict[tuple[str, ...], str] = {}
    for route in inventory.routes:
        parsed = urlparse(route if "://" in route else urljoin(inventory.app_url, route))
        segments = [segment for segment in (parsed.path or "/").split("/") if segment]
        if not segments:
            continue
        if segments[0] in {"pages", "reference", "apps", "challenges"} and len(segments) >= 2:
            key = (segments[0], segments[1])
        else:
            key = (segments[0],)
        current = section_map.get(key)
        candidate = parsed.path or "/"
        if current is None or len(candidate) < len(current):
            section_map[key] = candidate

    flows: list[dict] = []
    seen_names: set[str] = set()
    preferred_order = {"pages": 0, "apps": 1, "challenges": 2, "reference": 3}

    ranked_sections = sorted(
        section_map.items(),
        key=lambda item: (
            preferred_order.get(item[0][0], 9),
            len(item[0]),
            item[0],
        ),
    )

    for key, path in ranked_sections[:5]:
        if path == "/":
            continue
        section_label = "_".join(key)
        flow_name = f"explore_{_slugify_route_label(section_label)}"
        if flow_name in seen_names:
            continue
        seen_names.add(flow_name)
        title = _titleize_route_label(" ".join(key))
        target_url = urljoin(inventory.app_url, path)
        flows.append(
            {
                "flow_name": flow_name,
                "goal": f"Navigate to {target_url} and verify the {title} section loads and its primary examples are reachable.",
                "starting_url": target_url,
                "preconditions": [],
                "likely_assertions": [
                    f"URL contains {path}",
                    f"{title} navigation or examples are visible",
                ],
                "severity_if_broken": "medium",
                "confidence": 0.48,
                "business_criticality": "other",
            }
        )
    return flows


from blop.engine.dom_utils import extract_interactive_nodes_flat as _extract_interactive_nodes_flat


async def _capture_page_structure(page, max_nodes: int = 50) -> list[dict]:
    """Capture compact interactive structure. Uses ARIA tree first, falls back to DOM query."""
    # Tier 1: accessibility API with interesting_only=True
    try:
        accessibility = getattr(page, "accessibility", None)
        if accessibility and hasattr(accessibility, "snapshot"):
            snapshot = await accessibility.snapshot(interesting_only=True)
            if snapshot and isinstance(snapshot, dict):
                nodes = _extract_interactive_nodes_flat(snapshot, max_nodes=max_nodes)
                if nodes:
                    return nodes
            # Tier 2: interesting_only=False catches shadow-DOM and non-semantic components
            snapshot = await accessibility.snapshot(interesting_only=False)
            if snapshot and isinstance(snapshot, dict):
                nodes = _extract_interactive_nodes_flat(snapshot, max_nodes=max_nodes)
                if nodes:
                    return nodes
    except Exception:
        pass

    # Tier 3: page.aria_snapshot() text format (Playwright ≥1.50)
    try:
        aria_text = await page.aria_snapshot()
        if aria_text:
            nodes = _parse_aria_snapshot_text(aria_text, max_nodes=max_nodes)
            if nodes:
                return nodes
    except Exception:
        pass

    # Tier 4: DOM query — compute effective ARIA role from HTML semantics
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


def _parse_aria_snapshot_text(aria_text: str, max_nodes: int = 50) -> list[dict]:
    """Parse Playwright aria_snapshot() YAML-like text into role/name dicts."""
    import re
    _INTERACTIVE = {"button", "link", "textbox", "checkbox", "radio", "combobox",
                    "listbox", "menuitem", "tab", "switch", "searchbox", "spinbutton"}
    nodes = []
    for line in aria_text.splitlines():
        line = line.strip()
        if not line.startswith("- "):
            continue
        m = re.match(r'^-\s+(\w[\w-]*)\s+"([^"]+)"', line)
        if not m:
            continue
        role, name = m.group(1), m.group(2)
        nodes.append({"role": role, "name": name[:80]})
        if len(nodes) >= max_nodes:
            break
    return nodes


async def inventory_site(
    app_url: str,
    max_depth: int = 2,
    max_pages: int = BLOP_DISCOVERY_MAX_PAGES,
    same_origin_only: bool = True,
    profile_name: Optional[str] = None,
    seed_urls: Optional[list[str]] = None,
    include_url_pattern: Optional[str] = None,
    exclude_url_pattern: Optional[str] = None,
) -> SiteInventory:
    """Parallel crawl up to depth max_depth; extract buttons, links, forms, headings, and signals."""
    from playwright.async_api import async_playwright

    base_origin = urlparse(app_url).netloc
    all_buttons: list[dict] = []
    all_links: list[dict] = []
    all_forms: list[dict] = []
    all_headings: list[str] = []
    all_routes: set[str] = set()
    auth_signals: list[str] = []
    business_signals: list[str] = []
    page_structures: dict[str, list[dict]] = {}
    crawled_pages = 0
    adaptive_max_pages = max_pages
    hotspot_paths: set[str] = set()
    area_page_counts: dict[str, int] = {}
    error_count = 0
    crawl_started = time.monotonic()

    try:
        from blop.storage.sqlite import get_latest_context_graph
        previous_graph = await get_latest_context_graph(app_url, profile_name=profile_name)
        if previous_graph:
            for node in previous_graph.nodes:
                if node.node_type == "route" and node.confidence >= 0.7:
                    hotspot_paths.add(node.label)
    except Exception:
        _log.debug("Failed to load previous context graph for hotspot paths", exc_info=True)

    storage_state = await resolve_storage_state_for_profile(profile_name, allow_auto_env=False)
    worker_count = _resolve_discovery_worker_count(max_pages, storage_state=storage_state)
    active_worker_count = 1
    seeded_area_keys: list[str] = [_route_area_key(app_url)]
    if seed_urls:
        seeded_area_keys.extend(_route_area_key(seed) for seed in seed_urls if seed)
    seeded_area_keys = _dedupe_keep_order(seeded_area_keys)

    visited: set[str] = set()
    inflight: set[str] = set()
    frontier_urls: set[str] = set()
    frontier: list[_CrawlWorkItem] = []
    scheduled_pages = 0

    def _url_allowed(candidate: str) -> bool:
        if not candidate:
            return False
        if same_origin_only and urlparse(candidate).netloc != base_origin:
            return False
        if include_url_pattern and not re.search(include_url_pattern, candidate):
            return False
        if exclude_url_pattern and re.search(exclude_url_pattern, candidate):
            return False
        return True

    def _enqueue_url(candidate: str, *, depth: int, source_url: str) -> None:
        if not candidate or depth > max_depth or not _url_allowed(candidate):
            return
        if candidate in visited or candidate in inflight or candidate in frontier_urls:
            return
        frontier.append(
            _CrawlWorkItem(
                url=candidate,
                depth=depth,
                priority=_url_priority(candidate, business_signals, auth_signals, hotspot_paths),
                area_key=_route_area_key(candidate),
                source_url=source_url,
            )
        )
        frontier_urls.add(candidate)

    def _absorb_result(result: _CrawlPageResult) -> None:
        nonlocal crawled_pages, adaptive_max_pages, error_count
        if result.error:
            error_count += 1
            return

        crawled_pages += 1
        area_page_counts[result.area_key] = area_page_counts.get(result.area_key, 0) + 1
        all_buttons.extend(result.buttons)
        all_links.extend(result.links)
        all_forms.extend(result.forms)
        all_headings.extend(result.headings)
        for route in result.routes:
            all_routes.add(route)
        if result.nodes:
            page_structures[result.url] = result.nodes
        for signal in result.auth_signals:
            if signal not in auth_signals:
                auth_signals.append(signal)
        for signal in result.business_signals:
            if signal not in business_signals:
                business_signals.append(signal)
        adaptive_max_pages = _adaptive_budget(max_pages, business_signals, auth_signals)

        if result.depth >= max_depth:
            return
        for discovered_url in result.discovered_urls:
            _enqueue_url(discovered_url, depth=result.depth + 1, source_url=result.url)

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        try:
            bootstrap_urls = [app_url]
            if seed_urls:
                bootstrap_urls.extend(seed for seed in seed_urls if seed and seed != app_url)

            bootstrap_context = await _create_crawl_context(browser, storage_state=storage_state)
            bootstrap_page = await bootstrap_context.new_page()
            try:
                for bootstrap_url in bootstrap_urls:
                    if crawled_pages >= adaptive_max_pages or not _url_allowed(bootstrap_url):
                        continue
                    if bootstrap_url in visited:
                        continue
                    visited.add(bootstrap_url)
                    scheduled_pages += 1
                    bootstrap_item = _CrawlWorkItem(
                        url=bootstrap_url,
                        depth=0,
                        priority=1.0 if bootstrap_url == app_url else 0.9,
                        area_key=_route_area_key(bootstrap_url),
                        source_url=app_url,
                    )
                    result = await _crawl_one_page(bootstrap_page, bootstrap_item, worker_slot=0)
                    if result.error:
                        scheduled_pages = max(0, scheduled_pages - 1)
                    _absorb_result(result)
            finally:
                try:
                    await bootstrap_page.close()
                except Exception:
                    _log.debug("Failed to close bootstrap crawl page", exc_info=True)
                try:
                    await bootstrap_context.close()
                except Exception:
                    _log.debug("Failed to close bootstrap crawl context", exc_info=True)

            if frontier and crawled_pages < adaptive_max_pages:
                effective_worker_count = max(1, min(worker_count, len(frontier)))
                active_worker_count = effective_worker_count
                condition = asyncio.Condition()

                async def worker_loop(worker_slot: int) -> None:
                    nonlocal scheduled_pages
                    context = await _create_crawl_context(browser, storage_state=storage_state)
                    page = await context.new_page()
                    try:
                        while True:
                            async with condition:
                                while True:
                                    if crawled_pages >= adaptive_max_pages and not inflight:
                                        return
                                    if frontier and scheduled_pages < adaptive_max_pages:
                                        item = _choose_next_work_item(frontier, area_page_counts)
                                        frontier_urls.discard(item.url)
                                        inflight.add(item.url)
                                        visited.add(item.url)
                                        scheduled_pages += 1
                                        break
                                    if not frontier and not inflight:
                                        return
                                    await condition.wait()

                            result = await _crawl_one_page(page, item, worker_slot=worker_slot)

                            async with condition:
                                inflight.discard(item.url)
                                if result.error:
                                    scheduled_pages = max(crawled_pages, scheduled_pages - 1)
                                _absorb_result(result)
                                condition.notify_all()
                    finally:
                        try:
                            await page.close()
                        except Exception:
                            _log.debug("Failed to close crawl worker page", exc_info=True)
                        try:
                            await context.close()
                        except Exception:
                            _log.debug("Failed to close crawl worker context", exc_info=True)

                await asyncio.gather(
                    *(worker_loop(worker_slot) for worker_slot in range(1, effective_worker_count + 1))
                )
        finally:
            await browser.close()

    crawl_finished = time.monotonic()
    normalized_buttons = _normalize_inventory_buttons(all_buttons)[:30]
    normalized_links = _normalize_inventory_links(all_links)[:40]
    normalized_forms = _normalize_inventory_forms(all_forms)[:10]
    normalized_headings = sorted(_dedupe_keep_order(all_headings), key=str.lower)[:20]
    normalized_page_structures = {
        url: page_structures[url]
        for url in sorted(page_structures)
    }
    return SiteInventory(
        app_url=app_url,
        routes=sorted(list(all_routes))[:30],
        buttons=normalized_buttons,
        links=normalized_links,
        forms=normalized_forms,
        headings=normalized_headings,
        auth_signals=auth_signals,
        business_signals=business_signals,
        page_structures=normalized_page_structures,
        crawled_pages=crawled_pages,
        crawl_metadata={
            "mode": "parallel_section_aware" if active_worker_count > 1 else "sequential",
            "worker_count": active_worker_count,
            "seeded_area_keys": seeded_area_keys,
            "area_page_counts": dict(sorted(area_page_counts.items())),
            "timing_ms": int((crawl_finished - crawl_started) * 1000),
            "error_count": error_count,
        },
    )


async def get_page_structure(
    app_url: str,
    target_url: Optional[str] = None,
    profile_name: Optional[str] = None,
) -> dict:
    """Capture a single-page interactive structure snapshot via Playwright accessibility tree."""
    from playwright.async_api import async_playwright
    from blop.engine.interaction import wait_for_spa_ready

    storage_state = await resolve_storage_state_for_profile(profile_name, allow_auto_env=False)
    url = target_url or app_url

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        ctx_kwargs: dict = {}
        if storage_state:
            ctx_kwargs["storage_state"] = storage_state
        context = await browser.new_context(**ctx_kwargs)
        page = await context.new_page()
        try:
            try:
                await page.goto(url, wait_until="networkidle", timeout=20000)
            except Exception:
                await page.goto(url, timeout=20000)
            await wait_for_spa_ready(page, settle_ms=2500, timeout_ms=15000)
            nodes = await _capture_page_structure(page, max_nodes=80)
            return {
                "app_url": app_url,
                "requested_url": url,
                "current_url": page.url,
                "interactive_nodes": nodes,
                "interactive_node_count": len(nodes),
            }
        finally:
            await page.close()
            await browser.close()


async def plan_flows_from_inventory(
    inventory: SiteInventory,
    repo_context: Optional[str] = None,
    business_goal: Optional[str] = None,
    include_meta: bool = False,
) -> list[dict] | tuple[list[dict], dict]:
    """Send inventory to LLM with DISCOVER_PROMPT and return typed flows."""
    from blop.prompts import DISCOVER_PROMPT
    from blop.engine.llm_factory import make_planning_llm, make_message

    provider = os.getenv("BLOP_LLM_PROVIDER", "google").lower()
    fallback_meta = {"planning_fallback": False, "planning_error": None}
    heuristic_flows = _heuristic_flows_from_inventory(inventory)
    if inventory.crawled_pages <= 6 and len(inventory.routes) <= 4 and len(heuristic_flows) >= 3:
        fallback_meta = {
            "planning_fallback": True,
            "planning_error": "Used heuristic CTA planner for shallow inventory",
        }
        return (heuristic_flows, fallback_meta) if include_meta else heuristic_flows
    if provider == "google" and not os.getenv("GOOGLE_API_KEY"):
        fallback_meta = {
            "planning_fallback": True,
            "planning_error": "GOOGLE_API_KEY is not set",
        }
        flows = _fallback_flows(inventory.app_url, inventory=inventory)
        return (flows, fallback_meta) if include_meta else flows
    if provider == "anthropic" and not os.getenv("ANTHROPIC_API_KEY"):
        fallback_meta = {
            "planning_fallback": True,
            "planning_error": "ANTHROPIC_API_KEY is not set",
        }
        flows = _fallback_flows(inventory.app_url, inventory=inventory)
        return (flows, fallback_meta) if include_meta else flows
    if provider == "openai" and not os.getenv("OPENAI_API_KEY"):
        fallback_meta = {
            "planning_fallback": True,
            "planning_error": "OPENAI_API_KEY is not set",
        }
        flows = _fallback_flows(inventory.app_url, inventory=inventory)
        return (flows, fallback_meta) if include_meta else flows

    llm_kwargs: dict = {"temperature": 0.7, "max_output_tokens": 2000}
    # Extended thinking: enable for complex apps or when budget set
    thinking_budget = int(os.getenv("BLOP_THINKING_BUDGET", "0"))
    if thinking_budget > 0 and provider == "google":
        archetype_str = ""
        try:
            from blop.engine.context_graph import detect_app_archetype
            archetype_str = detect_app_archetype(inventory)
        except Exception:
            _log.debug("Failed to detect app archetype for thinking budget", exc_info=True)
        if archetype_str in ("editor_heavy", "checkout_heavy") or len(inventory.routes) > 12:
            llm_kwargs["thinking_budget"] = thinking_budget

    llm = make_planning_llm(**llm_kwargs)
    inventory_text = json.dumps(inventory.to_dict(), separators=(",", ":"))

    extra_context = ""
    if business_goal:
        extra_context += f"\nBusiness goal to prioritize: {business_goal}"
    if repo_context:
        extra_context += f"\nRepo context: {repo_context[:500]}"

    prompt = DISCOVER_PROMPT.format(
        app_url=inventory.app_url,
        inventory_text=inventory_text,
        extra_context=extra_context,
    )
    prompt = mask_text(prompt)

    try:
        response = await llm.ainvoke([make_message(prompt)])
        text = str(response.content) if hasattr(response, "content") else str(response)
        flows = _parse_flow_list(text)
        if flows:
            required_keys = {"flow_name", "goal"}
            valid_flows = []
            for f in flows:
                if isinstance(f, dict) and required_keys.issubset(f.keys()):
                    f.setdefault("starting_url", inventory.app_url)
                    f.setdefault("preconditions", [])
                    f.setdefault("likely_assertions", [])
                    f.setdefault("severity_if_broken", "medium")
                    f.setdefault("confidence", 0.7)
                    f.setdefault("business_criticality", "other")
                    valid_flows.append(f)
            if valid_flows:
                return (valid_flows, fallback_meta) if include_meta else valid_flows
    except Exception as e:
        _log.debug("LLM flow planning failed, using fallback flows", exc_info=True)
        fallback_meta = {
            "planning_fallback": True,
            "planning_error": f"{type(e).__name__}: {str(e)[:200]}",
        }

    flows = _fallback_flows(inventory.app_url, inventory=inventory)
    return (flows, fallback_meta) if include_meta else flows


def quality_gate_flows(inventory: SiteInventory, flows: list[dict]) -> tuple[bool, list[str]]:
    """Check that flows are specific and cover key signals. Returns (passed, warnings)."""
    warnings: list[str] = []

    generic_names = {"page_loads", "nav_links", "forms_work"}
    flow_names = {f.get("flow_name", "") for f in flows}

    if flow_names.issubset(generic_names):
        warnings.append("All flows are generic fallbacks; inventory scan may have returned no rich signals")
        return False, warnings

    # Auth signals must produce an auth flow
    if inventory.auth_signals:
        auth_kws = {"login", "auth", "signin", "signup", "register", "sign_in", "sign_up"}
        has_auth_flow = any(
            any(kw in f.get("flow_name", "").lower() or kw in f.get("goal", "").lower()
                for kw in auth_kws)
            for f in flows
        )
        if not has_auth_flow:
            warnings.append(
                f"Auth signals detected ({inventory.auth_signals[:3]}) but no auth flow proposed"
            )

    # Confidence gate
    confidences = [f.get("confidence", 0.5) for f in flows]
    if all(c < 0.4 for c in confidences):
        warnings.append("All flows have low confidence (< 0.4)")
        return False, warnings

    return True, warnings


async def discover_flows(
    app_url: str,
    repo_path: Optional[str] = None,
    profile_name: Optional[str] = None,
    business_goal: Optional[str] = None,
    max_depth: int = 2,
    max_pages: int = BLOP_DISCOVERY_MAX_PAGES,
    seed_urls: Optional[list[str]] = None,
    include_url_pattern: Optional[str] = None,
    exclude_url_pattern: Optional[str] = None,
    return_inventory: bool = False,
) -> dict:
    """Crawl site, plan flows, quality-gate, return rich discovery result."""
    inventory = await inventory_site(
        app_url,
        max_depth=max_depth,
        max_pages=max_pages,
        profile_name=profile_name,
        seed_urls=seed_urls,
        include_url_pattern=include_url_pattern,
        exclude_url_pattern=exclude_url_pattern,
    )
    try:
        from blop.storage.sqlite import save_site_inventory
        await save_site_inventory(app_url, inventory.to_dict())
    except Exception:
        _log.debug("Failed to save site inventory in discover_flows", exc_info=True)

    planning_meta: dict[str, Any] = {}
    if repo_path:
        flows = await _flows_from_repo(repo_path, app_url, inventory, business_goal)
    else:
        flows, planning_meta = await plan_flows_from_inventory(
            inventory,
            business_goal=business_goal,
            include_meta=True,
        )

    # Clamp to 3-8 flows
    flows = flows[:8]
    if len(flows) < 3:
        flows += _fallback_flows(app_url, inventory=inventory)[: 3 - len(flows)]

    passed, warnings = quality_gate_flows(inventory, flows)
    from blop.engine.context_graph import (
        build_context_graph,
        detect_app_archetype,
        diff_context_graph,
        get_context_graph_summary,
    )
    from blop.storage.sqlite import get_flow, get_latest_context_graph, list_flows, save_context_graph

    previous_graph = await get_latest_context_graph(app_url, profile_name=profile_name)
    flow_refs = await list_flows()
    recorded_flows = []
    for flow_ref in flow_refs:
        if flow_ref.get("app_url") != app_url:
            continue
        flow_obj = await get_flow(flow_ref["flow_id"])
        if flow_obj is not None:
            recorded_flows.append(flow_obj)
    current_graph = build_context_graph(
        app_url=app_url,
        inventory=inventory,
        flows=flows,
        profile_name=profile_name,
        recorded_flows=recorded_flows,
    )
    graph_diff = diff_context_graph(previous_graph, current_graph)
    await save_context_graph(current_graph)
    graph_summary = get_context_graph_summary(current_graph)

    result = {
        "app_url": app_url,
        "inventory_summary": {
            "routes_found": len(inventory.routes),
            "auth_signals": inventory.auth_signals,
            "business_signals": inventory.business_signals,
            "structured_pages": len(inventory.page_structures),
            "crawled_pages": inventory.crawled_pages,
            "app_archetype": detect_app_archetype(inventory),
        },
        "crawl_diagnostics": inventory.crawl_metadata,
        "flows": flows,
        "flow_count": len(flows),
        "quality": {
            "passed": passed,
            "warnings": warnings,
            "planning_fallback": planning_meta.get("planning_fallback", False) if not repo_path else False,
            "planning_error": planning_meta.get("planning_error") if not repo_path else None,
        },
        "context_graph": {
            "graph_id": current_graph.graph_id,
            "node_count": len(current_graph.nodes),
            "edge_count": len(current_graph.edges),
            "archetype": current_graph.archetype,
            "summary": graph_summary.model_dump(),
            "diff": graph_diff.model_dump(),
        },
    }
    if return_inventory:
        result["inventory"] = inventory.to_dict()
    return result


async def explore_site_inventory(
    app_url: str,
    profile_name: Optional[str] = None,
    max_depth: int = 2,
    max_pages: int = BLOP_DISCOVERY_MAX_PAGES,
    seed_urls: Optional[list[str]] = None,
    include_url_pattern: Optional[str] = None,
    exclude_url_pattern: Optional[str] = None,
) -> dict:
    """Inventory-only crawl for exploratory mapping without LLM flow planning."""
    from blop.engine.context_graph import detect_app_archetype

    inventory = await inventory_site(
        app_url=app_url,
        max_depth=max_depth,
        max_pages=max_pages,
        profile_name=profile_name,
        seed_urls=seed_urls,
        include_url_pattern=include_url_pattern,
        exclude_url_pattern=exclude_url_pattern,
    )
    try:
        from blop.storage.sqlite import save_site_inventory
        await save_site_inventory(app_url, inventory.to_dict())
    except Exception:
        _log.debug("Failed to save site inventory in explore_site_inventory", exc_info=True)
    return {
        "app_url": app_url,
        "inventory_summary": {
            "routes_found": len(inventory.routes),
            "auth_signals": inventory.auth_signals,
            "business_signals": inventory.business_signals,
            "structured_pages": len(inventory.page_structures),
            "crawled_pages": inventory.crawled_pages,
            "app_archetype": detect_app_archetype(inventory),
        },
        "crawl_diagnostics": inventory.crawl_metadata,
        "inventory": inventory.to_dict(),
    }


async def _flows_from_repo(
    repo_path: str,
    app_url: str,
    inventory: SiteInventory,
    business_goal: Optional[str] = None,
) -> list[dict]:
    import glob as glob_module

    patterns = [
        os.path.join(repo_path, "pages/**/*.tsx"),
        os.path.join(repo_path, "app/**/page.tsx"),
        os.path.join(repo_path, "src/**/*.tsx"),
    ]
    files: list[str] = []
    for pat in patterns:
        files.extend(glob_module.glob(pat, recursive=True))
    if not files:
        files = glob_module.glob(os.path.join(repo_path, "**/*.{ts,tsx,js,jsx}"), recursive=True)[:30]

    from blop.engine.llm_factory import make_planning_llm, make_message

    provider = os.getenv("BLOP_LLM_PROVIDER", "google").lower()
    has_key = (
        (provider == "google" and os.getenv("GOOGLE_API_KEY"))
        or (provider == "anthropic" and os.getenv("ANTHROPIC_API_KEY"))
        or (provider == "openai" and os.getenv("OPENAI_API_KEY"))
    )
    if not files or not has_key:
        return await plan_flows_from_inventory(inventory, business_goal=business_goal)

    llm = make_planning_llm(temperature=0.7, max_output_tokens=2000)
    file_list = "\n".join(files[:50])
    extra = f"\nBusiness goal: {business_goal}" if business_goal else ""

    prompt = f"""Based on these source files for {app_url}:{extra}
{file_list}

Generate 5-8 browser test flows as JSON with keys:
flow_name, goal, starting_url, preconditions, likely_assertions, severity_if_broken, confidence, business_criticality

business_criticality must be one of: revenue, activation, retention, support, other

Return only a JSON array:
[{{"flow_name": "...", "goal": "...", "starting_url": "...", "preconditions": [], "likely_assertions": ["..."], "severity_if_broken": "high", "confidence": 0.8, "business_criticality": "revenue"}}]"""

    prompt = mask_text(prompt)

    try:
        response = await llm.ainvoke([make_message(prompt)])
        text = str(response.content) if hasattr(response, "content") else str(response)
        flows = _parse_flow_list(text)
        if flows:
            valid = []
            for f in flows:
                if isinstance(f, dict) and {"flow_name", "goal"}.issubset(f.keys()):
                    f.setdefault("starting_url", app_url)
                    f.setdefault("preconditions", [])
                    f.setdefault("likely_assertions", [])
                    f.setdefault("severity_if_broken", "medium")
                    f.setdefault("confidence", 0.7)
                    f.setdefault("business_criticality", "other")
                    valid.append(f)
            if valid:
                return valid
    except Exception:
        _log.debug("LLM repo-based flow generation failed, falling back to inventory planning", exc_info=True)

    return await plan_flows_from_inventory(inventory, business_goal=business_goal)


def _fallback_flows(app_url: str, inventory: SiteInventory | None = None) -> list[dict]:
    flows = [
        {
            "flow_name": "page_loads",
            "goal": f"Navigate to {app_url} and verify the page loads",
            "starting_url": app_url,
            "preconditions": [],
            "likely_assertions": ["page title visible", "no 404 error"],
            "severity_if_broken": "blocker",
            "confidence": 0.3,
        },
        {
            "flow_name": "nav_links",
            "goal": f"Check all navigation links on {app_url} are functional",
            "starting_url": app_url,
            "preconditions": [],
            "likely_assertions": ["links respond", "no broken pages"],
            "severity_if_broken": "medium",
            "confidence": 0.3,
        },
        {
            "flow_name": "forms_work",
            "goal": f"Test any forms or input fields on {app_url}",
            "starting_url": app_url,
            "preconditions": [],
            "likely_assertions": ["form submits", "validation works"],
            "severity_if_broken": "medium",
            "confidence": 0.3,
        },
    ]
    if inventory is not None:
        flows.extend(_inventory_section_fallback_flows(inventory))
    deduped: list[dict] = []
    seen: set[str] = set()
    for flow in flows:
        name = str(flow.get("flow_name", "")).strip().lower()
        if not name or name in seen:
            continue
        seen.add(name)
        deduped.append(flow)
    return deduped[:8]
