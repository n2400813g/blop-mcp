"""Tests for engine/discovery.py."""

from __future__ import annotations

import asyncio
import json
import os
import time
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from blop.schemas import SiteInventory


@pytest.fixture
async def init_test_db():
    from blop.storage.sqlite import init_db

    await init_db()


@pytest.fixture
def mock_playwright_stack():
    mock_page = AsyncMock()
    mock_page.goto = AsyncMock()
    mock_page.evaluate = AsyncMock(return_value=[])
    mock_page.aria_snapshot = AsyncMock(return_value=None)

    mock_context = AsyncMock()
    mock_context.new_page.return_value = mock_page

    mock_browser = AsyncMock()
    mock_browser.new_context.return_value = mock_context

    mock_playwright = AsyncMock()
    mock_playwright.__aenter__ = AsyncMock(return_value=mock_playwright)
    mock_playwright.__aexit__ = AsyncMock(return_value=False)
    mock_playwright.chromium.launch.return_value = mock_browser

    return mock_playwright, mock_browser, mock_context, mock_page


class _FakeAccessibility:
    def __init__(self, page):
        self._page = page

    async def snapshot(self, interesting_only: bool = True):
        data = self._page.site_map.get(self._page.url, {})
        return data.get("accessibility")


class _FakePage:
    def __init__(self, site_map: dict, *, goto_log: list[str], delays: dict[str, float], failures: set[str]):
        self.site_map = site_map
        self.goto_log = goto_log
        self.delays = delays
        self.failures = failures
        self.url = ""
        self.accessibility = _FakeAccessibility(self)

    async def goto(self, url: str, **kwargs):
        self.goto_log.append(url)
        delay = self.delays.get(url, 0.0)
        if delay:
            await asyncio.sleep(delay)
        if url in self.failures:
            raise RuntimeError(f"boom:{url}")
        self.url = url

    async def wait_for_function(self, *args, **kwargs):
        return None

    async def wait_for_timeout(self, *args, **kwargs):
        return None

    async def wait_for_selector(self, *args, **kwargs):
        return None

    async def aria_snapshot(self):
        return None

    async def evaluate(self, script: str):
        data = self.site_map.get(self.url, {})
        if 'button, [role="button"]' in script:
            return data.get("buttons", [])
        if "Array.from(document.querySelectorAll('a[href]'))" in script and "href: el.href" in script:
            return data.get("links", [])
        if "Array.from(document.querySelectorAll('form'))" in script:
            return data.get("forms", [])
        if "Array.from(document.querySelectorAll('h1, h2, h3'))" in script:
            return data.get("headings", [])
        if "new URL(el.href).pathname" in script:
            return data.get("routes", [])
        return []

    async def close(self):
        return None


class _FakeContext:
    def __init__(self, site_map: dict, *, goto_log: list[str], delays: dict[str, float], failures: set[str]):
        self.site_map = site_map
        self.goto_log = goto_log
        self.delays = delays
        self.failures = failures

    async def new_page(self):
        return _FakePage(
            self.site_map,
            goto_log=self.goto_log,
            delays=self.delays,
            failures=self.failures,
        )

    async def close(self):
        return None


class _FakeBrowser:
    def __init__(self, site_map: dict, *, goto_log: list[str], delays: dict[str, float], failures: set[str]):
        self.site_map = site_map
        self.goto_log = goto_log
        self.delays = delays
        self.failures = failures
        self.new_context_calls: list[dict] = []

    async def new_context(self, **kwargs):
        self.new_context_calls.append(kwargs)
        return _FakeContext(
            self.site_map,
            goto_log=self.goto_log,
            delays=self.delays,
            failures=self.failures,
        )

    async def close(self):
        return None


class _FakePlaywright:
    def __init__(self, browser: _FakeBrowser):
        self.browser = browser
        self.chromium = MagicMock()
        self.chromium.launch = AsyncMock(return_value=browser)

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False


class _FakeLease:
    def __init__(self, browser, context, page):
        self.browser = browser
        self.context = context
        self.page = page

    async def close(self):
        await self.page.close()
        await self.context.close()


async def _make_fake_lease(browser, *, storage_state: str | None = None):
    kwargs = {"storage_state": storage_state} if storage_state else {}
    context = await browser.new_context(**kwargs)
    page = await context.new_page()
    return _FakeLease(browser, context, page)


def _site_graph() -> dict[str, dict]:
    return {
        "https://example.com": {
            "buttons": [{"text": "Dashboard", "href": "/billing"}],
            "links": [
                {"text": "Billing", "href": "https://example.com/billing"},
                {"text": "Settings", "href": "https://example.com/settings"},
                {"text": "Docs", "href": "https://example.com/docs"},
            ],
            "forms": [],
            "headings": ["Home"],
            "routes": ["/billing", "/settings", "/docs"],
            "accessibility": {"role": "WebArea", "children": [{"role": "button", "name": "Dashboard", "children": []}]},
        },
        "https://example.com/billing": {
            "buttons": [{"text": "Upgrade", "href": "/billing/details"}],
            "links": [{"text": "Plan details", "href": "https://example.com/billing/details"}],
            "forms": [],
            "headings": ["Billing"],
            "routes": ["/billing/details"],
            "accessibility": {"role": "WebArea", "children": [{"role": "button", "name": "Upgrade", "children": []}]},
        },
        "https://example.com/settings": {
            "buttons": [{"text": "Profile", "href": None}],
            "links": [],
            "forms": [
                {
                    "action": "https://example.com/settings",
                    "inputs": [{"type": "text", "name": "name", "placeholder": "Name", "label": ""}],
                }
            ],
            "headings": ["Settings"],
            "routes": [],
            "accessibility": {"role": "WebArea", "children": [{"role": "button", "name": "Profile", "children": []}]},
        },
        "https://example.com/docs": {
            "buttons": [{"text": "API Reference", "href": None}],
            "links": [],
            "forms": [],
            "headings": ["Docs"],
            "routes": [],
            "accessibility": {
                "role": "WebArea",
                "children": [{"role": "button", "name": "API Reference", "children": []}],
            },
        },
        "https://example.com/billing/details": {
            "buttons": [{"text": "Pay now", "href": None}],
            "links": [],
            "forms": [],
            "headings": ["Details"],
            "routes": [],
            "accessibility": {"role": "WebArea", "children": [{"role": "button", "name": "Pay now", "children": []}]},
        },
    }


def _fake_playwright_for_site(
    site_map: dict, *, delays: dict[str, float] | None = None, failures: set[str] | None = None
):
    goto_log: list[str] = []
    browser = _FakeBrowser(
        site_map,
        goto_log=goto_log,
        delays=delays or {},
        failures=failures or set(),
    )
    return _FakePlaywright(browser), browser, goto_log


@pytest.mark.asyncio
@pytest.mark.usefixtures("init_test_db")
async def test_discover_flows_returns_fallback_without_api_key(mock_playwright_stack):
    """Returns fallback flows when GOOGLE_API_KEY is not set."""
    from blop.engine.discovery import discover_flows

    _, mock_browser, _, _ = mock_playwright_stack
    lease = await _make_fake_lease(mock_browser)

    with patch.dict(os.environ, {}, clear=True):
        with patch("blop.engine.discovery.BROWSER_POOL.acquire", new_callable=AsyncMock, return_value=lease):
            result = await discover_flows("https://example.com")

    flows = result["flows"]
    assert len(flows) >= 3
    assert all("flow_name" in f and "goal" in f for f in flows)


@pytest.mark.asyncio
@pytest.mark.usefixtures("init_test_db")
async def test_discover_flows_fallback_spreads_public_site_across_sections():
    from blop.engine.discovery import discover_flows

    inventory = SiteInventory(
        app_url="https://testpages.eviltester.com/",
        routes=[
            "/",
            "/pages/input-elements/text-inputs/",
            "/pages/input-elements/basic-inputs/",
            "/pages/forms/html5-form-example/",
            "/pages/navigation/page-links/",
            "/apps/notes/simplenotes.html",
        ],
        buttons=[],
        links=[],
        forms=[],
        headings=[],
        auth_signals=[],
        business_signals=[],
        page_structures={},
        crawled_pages=12,
    )

    with patch.dict(os.environ, {}, clear=True):
        with patch("blop.engine.discovery.inventory_site", new_callable=AsyncMock, return_value=inventory):
            result = await discover_flows("https://testpages.eviltester.com/")

    flow_names = [flow["flow_name"] for flow in result["flows"]]
    goals = [flow["goal"] for flow in result["flows"]]
    assert "page_loads" in flow_names
    assert any("explore_pages_input_elements" == name for name in flow_names)
    assert any("explore_pages_forms" == name for name in flow_names)
    assert any("explore_pages_navigation" == name for name in flow_names)
    assert any("/pages/forms/html5-form-example/" in goal for goal in goals)


@pytest.mark.asyncio
@pytest.mark.usefixtures("init_test_db")
async def test_discover_flows_with_gemini_response(mock_playwright_stack):
    """Parses Gemini response into flow dicts."""
    from blop.engine.discovery import discover_flows

    gemini_response = json.dumps(
        [
            {
                "flow_name": "login_flow",
                "goal": "Log in with valid credentials",
                "likely_assertions": ["redirect to dashboard"],
            },
            {"flow_name": "nav_test", "goal": "Click main navigation links", "likely_assertions": ["pages load"]},
            {
                "flow_name": "form_submit",
                "goal": "Fill and submit contact form",
                "likely_assertions": ["success message"],
            },
        ]
    )

    mock_response = MagicMock()
    mock_response.content = gemini_response

    mock_llm = AsyncMock()
    mock_llm.ainvoke.return_value = mock_response

    _, mock_browser, _, _ = mock_playwright_stack
    lease = await _make_fake_lease(mock_browser)

    with patch.dict(os.environ, {"GOOGLE_API_KEY": "test_key"}):
        with patch("blop.engine.discovery.BROWSER_POOL.acquire", new_callable=AsyncMock, return_value=lease):
            with patch("browser_use.llm.ChatGoogle", return_value=mock_llm):
                result = await discover_flows("https://example.com")

    flows = result["flows"]
    assert len(flows) >= 3
    assert flows[0]["flow_name"] == "login_flow"


@pytest.mark.asyncio
@pytest.mark.usefixtures("init_test_db")
async def test_discover_flows_count_clamped(mock_playwright_stack):
    """Result is always 3-8 flows."""
    from blop.engine.discovery import discover_flows

    # Return more than 8
    many_flows = [{"flow_name": f"flow_{i}", "goal": f"Goal {i}", "likely_assertions": []} for i in range(15)]
    gemini_response = json.dumps(many_flows)

    mock_response = MagicMock()
    mock_response.content = gemini_response

    mock_llm = AsyncMock()
    mock_llm.ainvoke.return_value = mock_response

    _, mock_browser, _, _ = mock_playwright_stack
    lease = await _make_fake_lease(mock_browser)

    with patch.dict(os.environ, {"GOOGLE_API_KEY": "test_key"}):
        with patch("blop.engine.discovery.BROWSER_POOL.acquire", new_callable=AsyncMock, return_value=lease):
            with patch("browser_use.llm.ChatGoogle", return_value=mock_llm):
                result = await discover_flows("https://example.com")

    flows = result["flows"] if isinstance(result, dict) else result
    assert 3 <= len(flows) <= 8


@pytest.mark.asyncio
@pytest.mark.usefixtures("init_test_db")
async def test_discover_flows_includes_business_criticality(mock_playwright_stack):
    """Gemini response includes business_criticality → flows in result have it; missing field falls back to 'other'."""
    from blop.engine.discovery import discover_flows

    gemini_response = json.dumps(
        [
            {
                "flow_name": "checkout_with_credit_card",
                "goal": "Complete checkout",
                "likely_assertions": ["order confirmed"],
                "business_criticality": "revenue",
            },
            {
                "flow_name": "user_signup_onboarding",
                "goal": "Sign up",
                "likely_assertions": ["welcome screen"],
                "business_criticality": "activation",
            },
            {
                "flow_name": "help_center_search",
                "goal": "Search help",
                "likely_assertions": ["results appear"],
                # no business_criticality — should default to 'other'
            },
        ]
    )

    mock_response = MagicMock()
    mock_response.content = gemini_response
    mock_llm = AsyncMock()
    mock_llm.ainvoke.return_value = mock_response

    _, mock_browser, _, _ = mock_playwright_stack
    lease = await _make_fake_lease(mock_browser)

    with patch.dict(os.environ, {"GOOGLE_API_KEY": "test_key"}):
        with patch("blop.engine.discovery.BROWSER_POOL.acquire", new_callable=AsyncMock, return_value=lease):
            with patch("browser_use.llm.ChatGoogle", return_value=mock_llm):
                result = await discover_flows("https://example.com")

    flows = result["flows"]
    bc_values = {f.get("business_criticality") for f in flows}
    assert "revenue" in bc_values
    assert "activation" in bc_values
    # The flow without business_criticality should default to 'other'
    assert all(f.get("business_criticality") in {"revenue", "activation", "other"} for f in flows)


@pytest.mark.asyncio
@pytest.mark.usefixtures("init_test_db")
async def test_discover_flows_with_repo_path(tmp_path, mock_playwright_stack):
    """Uses repo path when provided."""
    from blop.engine.discovery import discover_flows

    # Create a dummy tsx file
    page_dir = tmp_path / "pages"
    page_dir.mkdir()
    (page_dir / "index.tsx").write_text("export default function Home() {}")

    fallback_response = json.dumps(
        [
            {"flow_name": "home_page", "goal": "Visit home page", "likely_assertions": ["page loads"]},
            {"flow_name": "nav_test", "goal": "Test navigation", "likely_assertions": ["links work"]},
            {"flow_name": "form_test", "goal": "Test forms", "likely_assertions": ["submit works"]},
        ]
    )

    mock_response = MagicMock()
    mock_response.content = fallback_response
    mock_llm = AsyncMock()
    mock_llm.ainvoke.return_value = mock_response

    _, mock_browser, _, _ = mock_playwright_stack
    lease = await _make_fake_lease(mock_browser)

    with patch.dict(os.environ, {"GOOGLE_API_KEY": "test_key"}):
        with patch("blop.engine.discovery.BROWSER_POOL.acquire", new_callable=AsyncMock, return_value=lease):
            with patch("browser_use.llm.ChatGoogle", return_value=mock_llm):
                result = await discover_flows("https://example.com", repo_path=str(tmp_path))

    flows = result["flows"] if isinstance(result, dict) else result
    assert len(flows) >= 3
    assert result["quality"]["planning_fallback"] is False
    assert result["quality"]["planning_error"] is None


@pytest.mark.asyncio
async def test_explore_site_inventory_includes_page_structures(mock_playwright_stack):
    """Inventory response includes compact per-page interactive ARIA nodes."""
    from blop.engine.discovery import explore_site_inventory

    _, mock_browser, _, mock_page = mock_playwright_stack
    lease = await _make_fake_lease(mock_browser)
    mock_page.url = "https://example.com"
    mock_page.evaluate = AsyncMock(side_effect=[[], [], [], [], []])
    mock_page.accessibility = MagicMock(
        snapshot=AsyncMock(
            return_value={
                "role": "WebArea",
                "name": "Example",
                "children": [
                    {"role": "button", "name": "Start Free Trial", "children": []},
                    {"role": "link", "name": "Pricing", "children": []},
                ],
            }
        )
    )

    with patch("blop.engine.discovery.BROWSER_POOL.acquire", new_callable=AsyncMock, return_value=lease):
        result = await explore_site_inventory("https://example.com")

    inventory = result["inventory"]
    assert "page_structures" in inventory
    assert "https://example.com" in inventory["page_structures"]
    assert inventory["page_structures"]["https://example.com"][0]["role"] == "button"


@pytest.mark.asyncio
async def test_get_page_structure_returns_interactive_nodes(mock_playwright_stack):
    """Single-page structure tool returns flattened ARIA interactive elements."""
    from blop.engine.discovery import get_page_structure

    _, mock_browser, _, mock_page = mock_playwright_stack
    lease = await _make_fake_lease(mock_browser)
    mock_page.url = "https://example.com/pricing"
    mock_page.wait_for_function = AsyncMock()
    mock_page.wait_for_timeout = AsyncMock()
    mock_page.accessibility = MagicMock(
        snapshot=AsyncMock(
            return_value={
                "role": "WebArea",
                "name": "Pricing",
                "children": [
                    {"role": "link", "name": "Upgrade", "children": []},
                    {"role": "button", "name": "Start", "children": []},
                ],
            }
        )
    )

    with patch("blop.engine.discovery.BROWSER_POOL.acquire", new_callable=AsyncMock, return_value=lease):
        result = await get_page_structure(
            app_url="https://example.com",
            target_url="https://example.com/pricing",
        )

    assert result["requested_url"] == "https://example.com/pricing"
    assert result["interactive_node_count"] == 2
    assert any(node["name"] == "Upgrade" for node in result["interactive_nodes"])


@pytest.mark.asyncio
async def test_inventory_site_parallel_and_single_worker_match_core_inventory(monkeypatch):
    from blop.engine.discovery import inventory_site

    site_map = _site_graph()
    _, browser, _ = _fake_playwright_for_site(site_map)
    lease = await _make_fake_lease(browser)

    monkeypatch.setattr("blop.engine.discovery.BLOP_DISCOVERY_CONCURRENCY", 1)
    with patch("blop.engine.discovery.BROWSER_POOL.acquire", new_callable=AsyncMock, return_value=lease):
        sequential = await inventory_site("https://example.com", max_depth=2, max_pages=5)

    _, browser_parallel, _ = _fake_playwright_for_site(site_map)
    lease_parallel = await _make_fake_lease(browser_parallel)
    monkeypatch.setattr("blop.engine.discovery.BLOP_DISCOVERY_CONCURRENCY", 3)
    with patch("blop.engine.discovery.BROWSER_POOL.acquire", new_callable=AsyncMock, return_value=lease_parallel):
        parallel = await inventory_site("https://example.com", max_depth=2, max_pages=5)

    assert sequential.routes == parallel.routes
    assert sequential.buttons == parallel.buttons
    assert sequential.links == parallel.links
    assert sequential.forms == parallel.forms
    assert sequential.headings == parallel.headings
    assert sequential.page_structures == parallel.page_structures


@pytest.mark.asyncio
async def test_inventory_site_prefers_distinct_sections_before_deeper_routes(monkeypatch):
    from blop.engine.discovery import inventory_site

    site_map = _site_graph()
    _, browser, goto_log = _fake_playwright_for_site(site_map)
    lease = await _make_fake_lease(browser)

    monkeypatch.setattr("blop.engine.discovery.BLOP_DISCOVERY_CONCURRENCY", 1)
    with patch("blop.engine.discovery.BROWSER_POOL.acquire", new_callable=AsyncMock, return_value=lease):
        await inventory_site("https://example.com", max_depth=2, max_pages=5)

    assert goto_log.index("https://example.com/settings") < goto_log.index("https://example.com/billing/details")


@pytest.mark.asyncio
async def test_inventory_site_reuses_storage_state_for_worker_contexts(monkeypatch):
    from blop.engine.discovery import inventory_site

    site_map = _site_graph()
    _, browser, _ = _fake_playwright_for_site(site_map)
    lease = await _make_fake_lease(browser, storage_state="/tmp/auth.json")

    monkeypatch.setattr("blop.engine.discovery.BLOP_DISCOVERY_CONCURRENCY", 3)
    with patch("blop.engine.discovery.BROWSER_POOL.acquire", new_callable=AsyncMock, return_value=lease):
        with patch(
            "blop.engine.discovery.resolve_storage_state_for_profile",
            new_callable=AsyncMock,
            return_value="/tmp/auth.json",
        ):
            await inventory_site("https://example.com", max_depth=2, max_pages=5, profile_name="prod")

    assert len(browser.new_context_calls) >= 2
    assert all(call.get("storage_state") == "/tmp/auth.json" for call in browser.new_context_calls)


@pytest.mark.asyncio
async def test_inventory_site_worker_failure_isolated(monkeypatch):
    from blop.engine.discovery import inventory_site

    site_map = _site_graph()
    _, browser, _ = _fake_playwright_for_site(site_map, failures={"https://example.com/billing"})
    lease = await _make_fake_lease(browser)

    monkeypatch.setattr("blop.engine.discovery.BLOP_DISCOVERY_CONCURRENCY", 3)
    with patch("blop.engine.discovery.BROWSER_POOL.acquire", new_callable=AsyncMock, return_value=lease):
        inventory = await inventory_site("https://example.com", max_depth=2, max_pages=5)

    assert inventory.crawled_pages >= 2
    assert inventory.crawl_metadata["error_count"] == 1
    assert "/settings" in inventory.routes


@pytest.mark.asyncio
async def test_inventory_site_parallel_mock_benchmark_is_faster(monkeypatch):
    from blop.engine.discovery import inventory_site

    site_map = _site_graph()
    delays = {
        "https://example.com": 0.05,
        "https://example.com/billing": 0.05,
        "https://example.com/settings": 0.05,
        "https://example.com/docs": 0.05,
    }

    _, browser_seq, _ = _fake_playwright_for_site(site_map, delays=delays)
    lease_seq = await _make_fake_lease(browser_seq)
    monkeypatch.setattr("blop.engine.discovery.BLOP_DISCOVERY_CONCURRENCY", 1)
    start = time.perf_counter()
    with patch("blop.engine.discovery.BROWSER_POOL.acquire", new_callable=AsyncMock, return_value=lease_seq):
        await inventory_site("https://example.com", max_depth=1, max_pages=4)
    sequential_time = time.perf_counter() - start

    _, browser_par, _ = _fake_playwright_for_site(site_map, delays=delays)
    lease_par = await _make_fake_lease(browser_par)
    monkeypatch.setattr("blop.engine.discovery.BLOP_DISCOVERY_CONCURRENCY", 3)
    start = time.perf_counter()
    with patch("blop.engine.discovery.BROWSER_POOL.acquire", new_callable=AsyncMock, return_value=lease_par):
        await inventory_site("https://example.com", max_depth=1, max_pages=4)
    parallel_time = time.perf_counter() - start

    assert parallel_time < sequential_time * 0.8


def test_parse_flow_list_accepts_python_literal_style_output():
    from blop.engine.discovery import _parse_flow_list

    payload = """Here are the flows:
    [{'flow_name': 'login_flow', 'goal': 'Log in'}, {'flow_name': 'upload_video', 'goal': 'Upload a video'}]
    """

    flows = _parse_flow_list(payload)

    assert flows is not None
    assert flows[0]["flow_name"] == "login_flow"
    assert flows[1]["goal"] == "Upload a video"


def test_heuristic_flows_from_inventory_uses_cta_buttons():
    from blop.engine.discovery import _heuristic_flows_from_inventory
    from blop.schemas import SiteInventory

    inventory = SiteInventory(
        app_url="https://example.com/app",
        routes=["/templates", "/shared"],
        buttons=[
            {"text": "AI Agent Try AI Agent"},
            {"text": "Blank project Create"},
            {"text": "Auto captions Add captions"},
        ],
        links=[{"text": "Shared with me", "href": "https://example.com/app/shared"}],
        forms=[],
        headings=["Start from a template"],
        auth_signals=[],
        business_signals=[],
        crawled_pages=3,
    )

    flows = _heuristic_flows_from_inventory(inventory)
    flow_names = {flow["flow_name"] for flow in flows}

    assert "enter_ai_agent" in flow_names
    assert "create_blank_project" in flow_names
    assert "start_caption_workflow" in flow_names
