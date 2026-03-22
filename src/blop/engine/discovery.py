"""Inventory-first discovery: BFS crawl → Gemini planning → quality gate."""
from __future__ import annotations

import ast
import json
import os
import re
from typing import Any, Optional
from urllib.parse import urlparse

from blop.config import BLOP_DISCOVERY_MAX_PAGES, BLOP_SPA_SETTLE_MS
from blop.engine.auth import resolve_storage_state_for_profile
from blop.engine.interaction import wait_for_spa_ready
from blop.engine.logger import get_logger
from blop.engine.secrets import mask_text
from blop.schemas import SiteInventory

_log = get_logger("discovery")


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
    """BFS crawl up to depth max_depth; extract buttons, links, forms, headings, and signals."""
    from playwright.async_api import async_playwright

    base_origin = urlparse(app_url).netloc
    visited: set[str] = set()
    queue: list[tuple[str, int, float]] = [(app_url, 0, 1.0)]
    if seed_urls:
        for seed in seed_urls:
            if seed and seed != app_url:
                queue.append((seed, 0, 0.9))

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

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        ctx_kwargs: dict = {}
        if storage_state:
            ctx_kwargs["storage_state"] = storage_state
        context = await browser.new_context(**ctx_kwargs)
        page = await context.new_page()

        try:
            while queue and crawled_pages < adaptive_max_pages:
                queue.sort(key=lambda item: item[2], reverse=True)
                url, depth, _priority = queue.pop(0)
                if url in visited:
                    continue
                if same_origin_only and urlparse(url).netloc != base_origin:
                    continue
                if include_url_pattern and not re.search(include_url_pattern, url):
                    continue
                if exclude_url_pattern and re.search(exclude_url_pattern, url):
                    continue
                visited.add(url)

                try:
                    try:
                        await page.goto(url, wait_until="domcontentloaded", timeout=15000)
                        await wait_for_spa_ready(
                            page,
                            settle_ms=max(BLOP_SPA_SETTLE_MS, 750),
                            timeout_ms=15000,
                        )
                    except Exception as first_error:
                        try:
                            await page.goto(url, timeout=15000)
                            await wait_for_spa_ready(
                                page,
                                settle_ms=max(BLOP_SPA_SETTLE_MS, 750),
                                timeout_ms=15000,
                            )
                        except Exception as second_error:
                            _log.debug(
                                "crawl_page_failed event=crawl_page_failed url=%s error_type=%s error_message=%s fallback_error_type=%s fallback_error_message=%s",
                                url,
                                type(first_error).__name__,
                                str(first_error)[:160],
                                type(second_error).__name__,
                                str(second_error)[:160],
                            )
                            continue

                    crawled_pages += 1

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

                    all_buttons.extend(page_buttons)
                    source_path = urlparse(url).path or "/"
                    for lnk in page_links:
                        lnk["source_route"] = source_path
                        lnk["source_url"] = url
                    all_links.extend(page_links)
                    all_forms.extend(page_forms)
                    all_headings.extend(page_headings)
                    for route in page_routes:
                        all_routes.add(route)
                    if page_nodes:
                        page_structures[url] = page_nodes

                    page_text_lower = " ".join(
                        [b.get("text", "") for b in page_buttons]
                        + [l.get("text", "") for l in page_links]
                        + page_headings
                    ).lower()

                    for signal in ("sign in", "login", "log in", "sign up", "register", "logout",
                                   "dashboard", "/auth", "/login", "/signup", "get started", "create account"):
                        if signal in page_text_lower and signal not in auth_signals:
                            auth_signals.append(signal)

                    for signal in ("pricing", "contact", "integration", "oauth", "checkout",
                                   "payment", "subscribe", "onboarding", "demo", "trial", "plans"):
                        if signal in page_text_lower and signal not in business_signals:
                            business_signals.append(signal)
                    adaptive_max_pages = _adaptive_budget(max_pages, business_signals, auth_signals)

                    routes_text = " ".join(page_routes).lower()
                    for signal in ("/pricing", "/contact", "/login", "/signup", "/auth", "/checkout", "/demo"):
                        if signal in routes_text and signal not in business_signals + auth_signals:
                            if signal in ("/login", "/signup", "/auth"):
                                if signal not in auth_signals:
                                    auth_signals.append(signal)
                            else:
                                if signal not in business_signals:
                                    business_signals.append(signal)

                    if depth < max_depth:
                        for link in page_links:
                            href = link.get("href", "")
                            if href and href.startswith("http"):
                                if urlparse(href).netloc != base_origin:
                                    continue
                                if include_url_pattern and not re.search(include_url_pattern, href):
                                    continue
                                if exclude_url_pattern and re.search(exclude_url_pattern, href):
                                    continue
                                if href not in visited:
                                    priority = _url_priority(href, business_signals, auth_signals, hotspot_paths)
                                    queue.append((href, depth + 1, priority))
                except Exception as e:
                    _log.debug(
                        "crawl_page_failed event=crawl_page_failed url=%s error_type=%s error_message=%s",
                        url,
                        type(e).__name__,
                        str(e)[:200],
                        exc_info=True,
                    )
        finally:
            try:
                await page.close()
            except Exception:
                _log.debug("Failed to close crawl page", exc_info=True)
            try:
                await context.close()
            except Exception:
                _log.debug("Failed to close browser context", exc_info=True)
            await browser.close()

    return SiteInventory(
        app_url=app_url,
        routes=sorted(list(all_routes))[:30],
        buttons=all_buttons[:30],
        links=all_links[:40],
        forms=all_forms[:10],
        headings=list(dict.fromkeys(all_headings))[:20],
        auth_signals=auth_signals,
        business_signals=business_signals,
        page_structures=page_structures,
        crawled_pages=crawled_pages,
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
        flows = _fallback_flows(inventory.app_url)
        return (flows, fallback_meta) if include_meta else flows
    if provider == "anthropic" and not os.getenv("ANTHROPIC_API_KEY"):
        fallback_meta = {
            "planning_fallback": True,
            "planning_error": "ANTHROPIC_API_KEY is not set",
        }
        flows = _fallback_flows(inventory.app_url)
        return (flows, fallback_meta) if include_meta else flows
    if provider == "openai" and not os.getenv("OPENAI_API_KEY"):
        fallback_meta = {
            "planning_fallback": True,
            "planning_error": "OPENAI_API_KEY is not set",
        }
        flows = _fallback_flows(inventory.app_url)
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

    flows = _fallback_flows(inventory.app_url)
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
        flows += _fallback_flows(app_url)[: 3 - len(flows)]

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


def _fallback_flows(app_url: str) -> list[dict]:
    return [
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
