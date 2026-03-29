"""Guided Browser-Use run that captures steps with selectors, screenshots, and assertions."""

from __future__ import annotations

import ast
import asyncio
import base64
import hashlib
import json
import os
import re
import uuid
from typing import Optional
from urllib.parse import urlparse

from blop.config import (
    BLOP_AGENT_MAX_ACTIONS_PER_STEP,
    BLOP_AGENT_MAX_FAILURES,
    BLOP_RECORDING_ENTRY_SETTLE_MS,
    BLOP_TEST_ID_ATTRIBUTE,
)
from blop.engine.browser_pool import BROWSER_POOL
from blop.engine.dom_utils import extract_interactive_nodes_flat
from blop.engine.evidence_policy import cap_artifact_paths, resolve_evidence_policy, should_capture_screenshot
from blop.engine.logger import get_logger
from blop.engine.page_state import PageStateCache
from blop.schemas import ApiExpectation, FlowStep, SemanticQuerySpec, StructuredAssertion

_log = get_logger("recording")
_LOW_SIGNAL_TARGET_TEXT = {
    "assert",
    "click",
    "done",
    "drag",
    "fill",
    "navigate",
    "select",
    "upload",
    "wait",
}


def _semantic_query_assertion(
    *,
    description: str,
    query: str,
    expected: str | None = None,
    extractor: str = "auto",
    target_selector: str | None = None,
    target_role: str | None = None,
    target_name: str | None = None,
) -> StructuredAssertion:
    return StructuredAssertion(
        assertion_type="semantic_query",
        description=description,
        expected=expected,
        target=target_selector,
        semantic_query=SemanticQuerySpec(
            query=query,
            expected=expected,
            extractor=extractor,
            target_selector=target_selector,
            target_role=target_role,
            target_name=target_name,
            match_mode="contains" if expected else "present",
        ),
    )


def infer_api_expectations(goal: str) -> list[ApiExpectation]:
    """Infer narrow journey-scoped API expectations from obvious business verbs."""
    lowered = (goal or "").lower()
    expectations: list[ApiExpectation] = []

    def _add(expectation: ApiExpectation) -> None:
        if any(existing.name == expectation.name for existing in expectations):
            return
        expectations.append(expectation)

    if any(token in lowered for token in ("checkout", "purchase", "billing", "upgrade", "subscription")):
        _add(
            ApiExpectation(
                name="checkout_api",
                url_contains="/api/checkout",
                methods=["POST"],
                required=True,
                description="Checkout flow should hit the checkout API successfully.",
            )
        )
    if any(token in lowered for token in ("sign in", "signin", "login", "log in", "authenticate")):
        _add(
            ApiExpectation(
                name="auth_api",
                url_contains="/api/",
                methods=["POST"],
                required=True,
                description="Authentication flow should issue at least one successful API POST.",
            )
        )
    if any(token in lowered for token in ("save", "publish", "update", "submit")):
        _add(
            ApiExpectation(
                name="save_or_publish_api",
                url_contains="/api/",
                methods=["POST", "PUT", "PATCH"],
                required=False,
                description="Save/publish flows should emit a successful write request.",
            )
        )
    return expectations


def _extract_goal_urls(goal: str) -> list[str]:
    matches = re.findall(r"https?://[^\s'\"),]+", goal or "")
    urls: list[str] = []
    for match in matches:
        cleaned = match.rstrip(".,;:")
        if cleaned and cleaned not in urls:
            urls.append(cleaned)
    return urls


def _extract_goal_text_expectations(goal: str) -> list[str]:
    expectations: list[str] = []
    patterns = (
        r"(?:shows?|display(?:s)?|contains?)\s+(?:the\s+)?text\s+['\"]([^'\"]+)['\"]",
        r"verify\s+(?:the\s+)?(?:page|homepage|screen)?\s*(?:shows?|contains?)\s+['\"]([^'\"]+)['\"]",
    )
    for pattern in patterns:
        for match in re.findall(pattern, goal or "", flags=re.IGNORECASE):
            text = str(match).strip()
            if text and text not in expectations:
                expectations.append(text)
    return expectations


def _build_public_page_assertions(
    *,
    goal: str,
    current_url: str,
    page_title: str,
    heading_text: str | None = None,
    page_body_text: str | None = None,
) -> list[tuple[str, Optional[StructuredAssertion]]]:
    assertions: list[tuple[str, Optional[StructuredAssertion]]] = []

    goal_urls = _extract_goal_urls(goal)
    if goal_urls:
        parsed = urlparse(goal_urls[0])
        expected = parsed.path or "/"
        if parsed.query:
            expected = f"{expected}?{parsed.query}"
        assertions.append(
            (
                f"URL contains {expected}",
                StructuredAssertion(
                    assertion_type="url_contains",
                    expected=expected,
                    description=f"URL contains {expected}",
                ),
            )
        )

    searchable_text = " ".join(
        part.strip().lower() for part in (heading_text or "", page_title or "", page_body_text or "") if part
    )
    for text in _extract_goal_text_expectations(goal):
        if searchable_text and text.lower() not in searchable_text:
            continue
        assertions.append(
            (
                f"Text '{text}' is visible",
                StructuredAssertion(
                    assertion_type="text_present",
                    expected=text,
                    description=f"Text '{text}' is visible",
                ),
            )
        )

    normalized_title = (page_title or "").strip()
    if normalized_title:
        assertions.append(
            (
                f"Page title contains {normalized_title}",
                StructuredAssertion(
                    assertion_type="page_title",
                    expected=normalized_title,
                    description=f"Page title contains {normalized_title}",
                ),
            )
        )

    deduped: list[tuple[str, Optional[StructuredAssertion]]] = []
    seen: set[tuple[str, str | None]] = set()
    for description, structured in assertions:
        key = (
            getattr(structured, "assertion_type", "semantic") if structured else "semantic",
            getattr(structured, "expected", None) if structured else description,
        )
        if key in seen:
            continue
        seen.add(key)
        deduped.append((description, structured))
    return deduped[:3]


def _looks_like_public_page_assertion_target(goal: str, current_url: str) -> bool:
    lowered_goal = (goal or "").lower()
    lowered_url = (current_url or "").lower()
    if any(token in lowered_goal for token in ("homepage", "public", "marketing", "landing page")):
        return True
    goal_urls = _extract_goal_urls(goal)
    if goal_urls:
        return True
    public_markers = ("/pages/", "/reference/", "/docs/", "/blog/", "/help/", "/challenges/")
    return any(marker in lowered_url for marker in public_markers) or lowered_url.rstrip("/").count("/") <= 2


def _escape_attr_value(value: str) -> str:
    return str(value).replace("\\", "\\\\").replace("'", "\\'")


def _selector_for_test_id(value: str, attr_name: str | None = None) -> str:
    attr = (attr_name or BLOP_TEST_ID_ATTRIBUTE or "data-testid").strip() or "data-testid"
    safe_attr = attr.replace("\\", "\\\\").replace("'", "\\'")
    return f"[{safe_attr}='{_escape_attr_value(value)}']"


def _selector_from_interacted_attrs(interacted_hint: dict[str, Optional[str]], action: str) -> Optional[str]:
    attrs = interacted_hint or {}
    testid = attrs.get("testid")
    if testid:
        return _selector_for_test_id(str(testid), attrs.get("testid_attr"))

    element_id = attrs.get("id")
    if element_id and re.match(r"^[A-Za-z][A-Za-z0-9_:\\-]*$", element_id):
        return f"#{element_id}"

    name_attr = attrs.get("name_attr")
    if name_attr:
        safe = str(name_attr).replace("'", "\\'")
        return f"[name='{safe}']"

    placeholder = attrs.get("placeholder")
    if placeholder:
        safe = str(placeholder).replace("'", "\\'")
        return f"[placeholder='{safe}']"

    href = attrs.get("href")
    if action == "click" and href:
        safe = str(href).replace("'", "\\'")
        return f"a[href='{safe}']"

    input_type = (attrs.get("input_type") or "").lower()
    if action == "fill" and input_type in {"email", "url", "tel", "password", "search", "number"}:
        return f"input[type='{input_type}']"
    if action == "fill" and input_type == "text":
        return "input[type='text']"
    return None


def _extract_interacted_attrs_from_description(description: str) -> dict[str, Optional[str]]:
    """Recover element attributes from the browser-use action repr when live attrs are unavailable."""
    if not description:
        return {}

    attrs_match = re.search(r"attributes=(\{.*?\})(?:[,)])", description)
    attrs: dict[str, Optional[str]] = {}
    if attrs_match:
        try:
            parsed = ast.literal_eval(attrs_match.group(1))
        except Exception:
            parsed = {}
        if isinstance(parsed, dict):
            attrs.update(parsed)

    # The browser-use repr is often truncated; recover the most useful keys directly.
    for key in ("id", "name", "type", "placeholder", "href", "aria-label", "role"):
        match = re.search(rf"'{re.escape(key)}': '([^']+)'", description)
        if match and key not in attrs:
            attrs[key] = match.group(1)

    if not attrs:
        return {}

    node_name_match = re.search(r"node_name='([^']+)'", description)
    node_name = node_name_match.group(1).lower() if node_name_match else ""

    return {
        "id": attrs.get("id"),
        "testid": attrs.get(BLOP_TEST_ID_ATTRIBUTE)
        or attrs.get("data-testid")
        or attrs.get("data-cy")
        or attrs.get("data-test"),
        "testid_attr": (
            BLOP_TEST_ID_ATTRIBUTE
            if attrs.get(BLOP_TEST_ID_ATTRIBUTE)
            else "data-testid"
            if attrs.get("data-testid")
            else "data-cy"
            if attrs.get("data-cy")
            else "data-test"
            if attrs.get("data-test")
            else None
        ),
        "placeholder": attrs.get("placeholder"),
        "name_attr": attrs.get("name"),
        "href": attrs.get("href"),
        "input_type": attrs.get("type"),
        "name": (attrs.get("aria-label") or attrs.get("placeholder") or attrs.get("name") or attrs.get("title")),
        "role": attrs.get("role"),
        "node_name": node_name,
    }


def _merge_interacted_hints(
    primary: dict[str, Optional[str]],
    fallback: dict[str, Optional[str]],
) -> dict[str, Optional[str]]:
    merged = dict(fallback or {})
    merged.update({key: value for key, value in (primary or {}).items() if value})
    return merged


# Shared agent instructions used by both recording and goal-fallback regression agents.
# Kept here so both contexts stay in sync without duplication.
class BlopActions:
    """Custom browser actions for common QA patterns (toast assertions, modal dismissal)."""

    @staticmethod
    async def assert_toast_visible(page, message_substring: str = "") -> str:
        """Wait up to 5s for a toast/alert/snackbar to appear. Returns 'visible' or 'not_found'."""
        selectors = [
            "[role='alert']",
            "[class*='toast']",
            "[class*='snackbar']",
            "[class*='notification']",
        ]
        import asyncio as _asyncio

        for _ in range(10):
            for sel in selectors:
                try:
                    el = await page.query_selector(sel)
                    if el:
                        text = (await el.inner_text()) or ""
                        if not message_substring or message_substring.lower() in text.lower():
                            return "visible"
                except Exception:
                    _log.debug("query toast element for visibility check", exc_info=True)
            await _asyncio.sleep(0.5)
        return "not_found"

    @staticmethod
    async def dismiss_modal(page) -> str:
        """Click the first visible close/dismiss button inside a modal. Returns 'dismissed' or 'no_modal'."""
        close_selectors = [
            "[role='dialog'] button[aria-label*='close' i]",
            "[role='dialog'] button[aria-label*='dismiss' i]",
            "[role='dialog'] button[aria-label*='cancel' i]",
            "[class*='modal'] button[aria-label*='close' i]",
            "[class*='modal'] .close",
        ]
        for sel in close_selectors:
            try:
                el = await page.query_selector(sel)
                if el and await el.is_visible():
                    await el.click()
                    return "dismissed"
            except Exception:
                _log.debug("click modal close button", exc_info=True)
        return "no_modal"


SPA_AGENT_RULES = (
    "IMPORTANT — SPA & web-component rules: "
    "(1) After clicking a project card or nav link, wait 3–5 seconds before asserting that content is visible — views load asynchronously in SPAs. "
    "(2) If the page shows a loading spinner or skeleton, wait for it to disappear before proceeding. "
    "(3) Do NOT retry the same click if the URL has already changed — navigation may have succeeded even if content is still loading. "
    "(4) If a standard selector fails, try scrolling the element into view, then retry once. "
    "(5) Some UI elements live inside shadow DOM / web components — if they are not found by normal means, describe them for vision-based interaction. "
    "(6) If an element is not found, check if it is inside a shadow DOM or web component. "
    "(7) CANVAS / WEBGL APPS: If the primary UI renders into a <canvas> element (design tools, diagram builders, creative editors, game engines, etc.), "
    "wait up to 30 seconds for the application toolbar to appear — WebGL and WASM initialisation takes 15–30 seconds. "
    "A dark or blank canvas is NOT a failure; it means the application is still initialising. "
    "(8) In canvas-based applications, only assert DOM chrome elements: toolbar buttons, menu bar items, top-level controls, or the canvas element itself. "
    "Do not attempt to interact with content rendered inside the canvas — it is not in the accessibility tree. "
    "(9) The canvas application has loaded successfully when at least one actionable toolbar element (e.g. Export, Publish, File menu) is visible in the DOM."
)


async def record_flow(
    app_url: str,
    goal: str,
    storage_state: Optional[str],
    headless: bool = False,
    run_id: Optional[str] = None,
) -> list[FlowStep]:
    """Run a Browser-Use agent for `goal`; capture each action with selector, target_text,
    dom_fingerprint, per-step screenshot, and final assertion steps."""
    from browser_use import Agent, BrowserSession

    from blop.engine.browser import make_browser_profile
    from blop.engine.llm_factory import make_agent_llm, make_planning_llm
    from blop.storage import files as file_store

    evidence_policy = resolve_evidence_policy()
    llm = make_agent_llm(role="agent")
    page_extraction_llm = make_planning_llm(temperature=0.0, max_output_tokens=256, role="summary")
    browser_profile = make_browser_profile(headless=headless, storage_state=storage_state)
    browser_session = BrowserSession(browser_profile=browser_profile)

    recording_id = run_id or uuid.uuid4().hex
    steps: list[FlowStep] = []
    step_counter = 0
    record_error: Exception | None = None

    # Initial navigation step
    steps.append(
        FlowStep(
            step_id=step_counter,
            action="navigate",
            value=app_url,
            description=f"Navigate to {app_url}",
            url_after=app_url,
        )
    )
    step_counter += 1

    # Pre-agent discovery: use raw Playwright to extract SPA-internal links
    # that a browser-use click can't reliably trigger (e.g. React Router cards).
    # Only run when the goal implies navigating to a sub-page (not a dashboard/list page).
    # Skip for "create new" flows — those must start from the dashboard, not a pre-resolved URL.
    _deep_keywords = {"editor", "project", "workspace", "open", "enter", "launch", "canvas"}
    _create_keywords = {"create", "new project", "new file", "start fresh", "make new"}
    _is_create_flow = any(kw in goal.lower() for kw in _create_keywords)
    _needs_deep = not _is_create_flow and any(kw in goal.lower() for kw in _deep_keywords)
    entry_url_hint: Optional[str] = None
    if _needs_deep:
        try:
            entry_url_hint = await _resolve_spa_entry_url(
                app_url=app_url,
                storage_state=storage_state,
                headless=headless,
                goal=goal,
            )
        except Exception:
            _log.debug("resolve SPA entry URL for goal", exc_info=True)
    if entry_url_hint and entry_url_hint != app_url:
        steps.append(
            FlowStep(
                step_id=step_counter,
                action="navigate",
                value=entry_url_hint,
                description=f"Navigate to discovered entry URL: {entry_url_hint}",
                url_after=entry_url_hint,
            )
        )
        step_counter += 1
        # The browser was already navigated to entry_url_hint by _discover_entry_url.
        # Tell the agent to start from there — no need to navigate again.
        task = f"Navigate to {entry_url_hint} then: {goal}"
    else:
        task = f"Navigate to {app_url} then: {goal}"

    # Provide runtime auth guidance without ever including raw credentials.
    from blop.engine.auth_prompt import append_runtime_auth_guidance

    task = append_runtime_auth_guidance(task)
    step_screenshots: list[str] = []
    screenshot_task: Optional[asyncio.Task] = None
    step_idx_counter = [0]
    page_state = PageStateCache()

    async def _poll_screenshots():
        while True:
            try:
                if not should_capture_screenshot(evidence_policy, "periodic"):
                    return
                await asyncio.sleep(evidence_policy.screenshot_interval_secs)
                if step_idx_counter[0] >= evidence_policy.max_screenshots:
                    return
                ctx = getattr(browser_session, "context", None)
                if ctx and ctx.pages:
                    shot_path = file_store.screenshot_path(recording_id, "record", step_idx_counter[0])
                    await ctx.pages[0].screenshot(path=shot_path)
                    step_screenshots.append(shot_path)
                    step_idx_counter[0] += 1
            except asyncio.CancelledError:
                break
            except Exception:
                _log.debug("poll screenshot capture", exc_info=True)

    try:
        agent_kwargs: dict = dict(
            task=task,
            llm=llm,
            browser_session=browser_session,
            use_vision="auto",
            use_judge=False,
            flash_mode=True,
            page_extraction_llm=page_extraction_llm,
            max_failures=BLOP_AGENT_MAX_FAILURES,
            max_actions_per_step=BLOP_AGENT_MAX_ACTIONS_PER_STEP,
            extend_system_message=(
                "You are a QA recorder. You MUST take explicit browser actions (click, fill, navigate) "
                "to complete every step described in the task. "
                "Do NOT call 'done' until you have visually confirmed that each requested action has been performed. "
                "After navigating to the URL, always look for and interact with the UI elements described. "
                + SPA_AGENT_RULES
            ),
        )
        # Attach BlopActions helpers if browser-use Agent supports additional_tools
        try:
            agent_kwargs["additional_tools"] = [
                BlopActions.assert_toast_visible,
                BlopActions.dismiss_modal,
            ]
        except Exception:
            _log.debug("attach BlopActions to agent", exc_info=True)
        agent = Agent(**agent_kwargs)
        if should_capture_screenshot(evidence_policy, "periodic"):
            screenshot_task = asyncio.create_task(_poll_screenshots())

        try:
            history = await agent.run(max_steps=50)
        finally:
            if screenshot_task is not None:
                screenshot_task.cancel()
                try:
                    await screenshot_task
                except asyncio.CancelledError:
                    pass

        # Get the active page reference for ARIA/testid extraction
        page_ref = None
        try:
            ctx = getattr(browser_session, "context", None)
            if ctx and ctx.pages:
                page_ref = ctx.pages[0]
        except Exception:
            _log.debug("get page reference from browser session", exc_info=True)

        all_actions = history.model_actions() if hasattr(history, "model_actions") else []
        if os.getenv("BLOP_DEBUG"):
            try:
                with open("/tmp/blop_debug.log", "a") as _dbg:
                    _dbg.write(
                        f"[blop-debug] agent history: {len(all_actions)} actions, is_done={getattr(history, 'is_done', lambda: '?')()}\n"
                    )
                    for _i, _a in enumerate(all_actions[:5]):
                        _dbg.write(f"  action[{_i}]: {str(_a)[:120]}\n")
                    try:
                        errs = history.errors()
                        _dbg.write(f"  errors(): {str(errs)[:500]}\n")
                    except Exception as _ee:
                        _dbg.write(f"  errors() failed: {_ee}\n")
                    _dbg.write(f"  history_len: {len(getattr(history, 'history', []))}\n")
            except Exception:
                _log.debug("debug log agent history", exc_info=True)
        if hasattr(history, "model_actions"):
            for i, action in enumerate(history.model_actions()):
                selector: Optional[str] = None
                value: Optional[str] = None
                target_text: Optional[str] = None
                url_before: Optional[str] = None
                url_after: Optional[str] = None
                interacted_xpath: Optional[str] = None

                # model_actions() returns list[dict] with ALL action keys (most None).
                # Find the non-None key to get the actual action type.
                if isinstance(action, dict):
                    action_name = next(
                        (k for k, v in action.items() if k != "interacted_element" and v is not None), "click"
                    )
                    params = action.get(action_name) or {}
                    interacted = action.get("interacted_element")
                    interacted_hint = _extract_interacted_element_hint(interacted)

                    idx = params.get("index") if isinstance(params, dict) else None
                    if idx is not None:
                        selector = f"[data-browser-use-index='{idx}']"
                    if isinstance(params, dict) and "text" in params:
                        value = str(params["text"])
                    if isinstance(params, dict) and "url" in params:
                        value = str(params["url"])
                        url_after = value

                    # Keep interacted xpath as a fallback locator only; we prefer semantic selectors.
                    if interacted is not None:
                        try:
                            interacted_xpath = interacted.xpath if hasattr(interacted, "xpath") else None
                            elem_text = (
                                interacted.get_meaningful_text_for_llm()
                                if hasattr(interacted, "get_meaningful_text_for_llm")
                                else None
                            )
                            if elem_text:
                                target_text = elem_text[:100]
                        except Exception:
                            _log.debug("extract interacted element xpath and text", exc_info=True)

                    desc = str(action)[:400]
                    interacted_hint = _merge_interacted_hints(
                        interacted_hint,
                        _extract_interacted_attrs_from_description(desc),
                    )
                    if not target_text:
                        # Prefer meaningful param values over the raw dict repr (which would
                        # produce the action key name, e.g. "write_file", as the target text).
                        if isinstance(params, dict):
                            param_text = (
                                params.get("text")
                                or params.get("description")
                                or params.get("query")
                                or params.get("value")
                            )
                            if param_text:
                                target_text = str(param_text)[:100]
                        if not target_text:
                            target_text = _extract_target_text(desc)
                else:
                    # Fallback for typed action objects (older browser-use versions)
                    action_name = type(action).__name__.lower() if action else "click"
                    interacted_xpath = None
                    if hasattr(action, "index") and action.index is not None:
                        selector = f"[data-browser-use-index='{action.index}']"
                    if hasattr(action, "text") and action.text:
                        value = str(action.text)
                    if hasattr(action, "url") and action.url:
                        value = str(action.url)
                        url_after = value
                    desc = str(action)[:400] if action else ""
                    target_text = _extract_target_text(desc)

                mapped = _map_action(action_name)
                if not mapped:
                    continue
                screenshot_path = step_screenshots[i] if i < len(step_screenshots) else None

                # Capture semantic locators (ARIA role/name, testid, label)
                aria_role: Optional[str] = None
                aria_name: Optional[str] = None
                aria_snapshot: Optional[str] = None
                testid_selector: Optional[str] = None
                label_text: Optional[str] = None
                dom_role: Optional[str] = interacted_hint.get("role") if isinstance(interacted_hint, dict) else None
                dom_name: Optional[str] = interacted_hint.get("name") if isinstance(interacted_hint, dict) else None

                if page_ref is not None and mapped != "navigate":
                    if _is_brittle_selector(selector):
                        selector = None
                    locator_reference = interacted_xpath or selector
                    locator_kind = "xpath" if interacted_xpath else "css"
                    if locator_reference:
                        testid_selector, label_text, locator_role, locator_name = await _capture_locator_attrs(
                            page_ref,
                            locator_reference,
                            mapped,
                            locator_kind=locator_kind,
                        )
                        dom_role = locator_role or dom_role
                        dom_name = locator_name or dom_name
                        if testid_selector:
                            selector = testid_selector
                        elif not selector and interacted_xpath:
                            selector = interacted_xpath

                    target_text = _prefer_semantic_target_text(
                        target_text,
                        dom_name,
                        label_text,
                        interacted_hint.get("target_text") if isinstance(interacted_hint, dict) else None,
                    )

                    page_state.invalidate(page_ref)
                    aria_role, aria_name, aria_snapshot = await _capture_aria_for_element(
                        page_ref, target_text, page_state=page_state
                    )

                    # Use DOM-computed role/name as fallback when accessibility snapshot returned empty.
                    if not aria_role and dom_role:
                        aria_role = dom_role
                    if not aria_name and dom_name:
                        aria_name = dom_name
                    if not target_text:
                        target_text = _prefer_semantic_target_text(None, aria_name, dom_name, label_text)

                if not selector and isinstance(interacted_hint, dict):
                    selector = _selector_from_interacted_attrs(interacted_hint, mapped)
                if not label_text and isinstance(interacted_hint, dict):
                    label_text = interacted_hint.get("placeholder") or interacted_hint.get("name") or label_text

                if _is_brittle_selector(selector):
                    selector = None

                steps.append(
                    FlowStep(
                        step_id=step_counter,
                        action=mapped,
                        selector=selector,
                        value=value,
                        description=desc,
                        target_text=target_text,
                        dom_fingerprint=_compute_fingerprint(mapped, selector, target_text, i),
                        url_before=url_before,
                        url_after=url_after,
                        screenshot_path=screenshot_path,
                        aria_role=aria_role,
                        aria_name=aria_name,
                        aria_snapshot=aria_snapshot,
                        testid_selector=testid_selector,
                        label_text=label_text,
                        replay_recipe=_build_replay_recipe(
                            action=mapped,
                            selector=selector,
                            target_text=target_text,
                            testid_selector=testid_selector,
                            label_text=label_text,
                            aria_role=aria_role,
                            aria_name=aria_name,
                        ),
                    )
                )
                step_counter += 1

        # Take final screenshot and generate assertion steps
        try:
            ctx = getattr(browser_session, "context", None)
            if ctx and ctx.pages:
                final_page = ctx.pages[0]
                final_path = None
                if should_capture_screenshot(evidence_policy, "final"):
                    final_path = file_store.screenshot_path(recording_id, "record", 999)
                    await final_page.screenshot(path=final_path)

                # Capture ARIA context for richer assertion generation
                page_state.invalidate(final_page)
                aria_context = await _get_page_aria_context(final_page, page_state=page_state)

                assertion_steps = await _generate_assertions_from_screenshot(
                    final_page, goal, aria_context=aria_context
                )
                for assertion_text, structured in assertion_steps:
                    steps.append(
                        FlowStep(
                            step_id=step_counter,
                            action="assert",
                            description=assertion_text,
                            value=assertion_text,
                            screenshot_path=final_path,
                            structured_assertion=structured,
                        )
                    )
                    step_counter += 1
        except Exception:
            _log.debug("capture final screenshot and generate assertions", exc_info=True)

    except Exception as e:
        record_error = e
        _log.debug(
            "record_failed recording_id=%s goal=%s error=%s",
            recording_id,
            goal[:120],
            str(e),
            exc_info=True,
        )
        # Keep one explicit artifact for debugging failed recordings when possible.
        try:
            ctx = getattr(browser_session, "context", None)
            if ctx and ctx.pages and should_capture_screenshot(evidence_policy, "failure"):
                failure_path = file_store.screenshot_path(recording_id, "record", 998)
                await ctx.pages[0].screenshot(path=failure_path)
        except Exception:
            _log.debug("capture recording failure screenshot", exc_info=True)
    finally:
        try:
            await browser_session.aclose()
        except Exception:
            _log.debug("close browser session", exc_info=True)

    if record_error is not None:
        raise RuntimeError(
            f"record_flow failed for recording_id={recording_id}: {type(record_error).__name__}: {record_error}"
        ) from record_error

    # Guarantee at least a navigation + assertion
    if len(steps) <= 1:
        steps.append(
            FlowStep(
                step_id=step_counter,
                action="assert",
                description=goal,
                value=goal,
            )
        )

    artifact_limit = min(evidence_policy.max_screenshots, evidence_policy.artifact_cap)
    capped_screenshots = cap_artifact_paths(step_screenshots, limit=artifact_limit)
    for step in steps:
        if step.screenshot_path and step.screenshot_path not in capped_screenshots:
            step.screenshot_path = None

    return steps


async def _generate_assertions_from_screenshot(
    page,
    goal: str,
    aria_context: str = "",
) -> list[tuple[str, Optional[StructuredAssertion]]]:
    """Ask the configured LLM to generate 1-3 structured assertions based on the final page screenshot.

    Returns a list of (assertion_text, StructuredAssertion | None) tuples.
    Falls back to plain-string assertions if structured parsing fails.
    """
    current_url = ""
    page_title = ""
    heading_text = ""
    try:
        current_url = page.url or ""
    except Exception:
        current_url = ""
    try:
        page_title = await page.title() or ""
    except Exception:
        page_title = ""
    try:
        heading_text = (await page.inner_text("h1") or "").strip()
    except Exception:
        heading_text = ""
    try:
        page_body_text = (await page.evaluate("() => document.body.innerText") or "").strip()
    except Exception:
        page_body_text = ""

    if _looks_like_public_page_assertion_target(goal, current_url):
        deterministic_public = _build_public_page_assertions(
            goal=goal,
            current_url=current_url,
            page_title=page_title,
            heading_text=heading_text or None,
            page_body_text=page_body_text or None,
        )
        if deterministic_public:
            return deterministic_public

    from blop.config import check_llm_api_key

    has_key, _ = check_llm_api_key()
    if not has_key:
        return [
            (
                f"Page title contains {page_title or 'the expected page'}",
                StructuredAssertion(
                    assertion_type="page_title",
                    expected=page_title or "expected page",
                    description=f"Page title contains {page_title or 'the expected page'}",
                ),
            )
        ]

    try:
        from blop.engine.llm_factory import make_planning_llm

        img_bytes = await page.screenshot(type="jpeg", quality=85)
        b64 = base64.b64encode(img_bytes).decode()

        aria_section = f"\nARIA tree of final page state:\n{aria_context}\n" if aria_context else ""

        llm = make_planning_llm(temperature=0.1, max_output_tokens=600, role="assertion")
        prompt = f"""Look at this screenshot of a web page after completing: "{goal}"
{aria_section}
Generate 1-3 specific, verifiable assertions about what should be visible.

Return ONLY a JSON array where each item has these fields:
- type: one of "text_present" | "element_visible" | "url_contains" | "page_title" | "semantic"
- target: CSS selector, ARIA label, or URL substring (null for semantic)
- expected: expected text/value (null if not applicable)
- description: plain English assertion string

Prefer deterministic types (text_present, element_visible, url_contains) over "semantic" when possible.
Use "semantic" only for assertions requiring visual judgment.

Example:
[
  {{"type": "text_present", "target": "h1", "expected": "Dashboard", "description": "Dashboard heading is visible"}},
  {{"type": "url_contains", "target": null, "expected": "/dashboard", "description": "URL contains /dashboard"}}
]
"""
        from blop.engine.secrets import mask_text

        prompt = mask_text(prompt)

        from langchain_core.messages import HumanMessage

        response = await llm.ainvoke(
            [
                HumanMessage(
                    content=[
                        {"type": "text", "text": prompt},
                        {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{b64}"}},
                    ]
                )
            ]
        )
        text = str(response.content) if hasattr(response, "content") else str(response)
        m = re.search(r"\[.*?\]", text, re.DOTALL)
        if m:
            raw_list = json.loads(m.group())
            if isinstance(raw_list, list) and raw_list:
                results = []
                for item in raw_list[:3]:
                    if isinstance(item, str):
                        results.append((item, None))
                    elif isinstance(item, dict):
                        desc = item.get("description") or item.get("expected") or str(item)
                        try:
                            if item.get("type") == "semantic":
                                sa = _semantic_query_assertion(
                                    description=desc,
                                    query=desc,
                                    expected=item.get("expected"),
                                    target_selector=item.get("target"),
                                )
                            else:
                                sa = StructuredAssertion(
                                    assertion_type=item.get("type", "semantic"),
                                    target=item.get("target"),
                                    expected=item.get("expected"),
                                    description=desc,
                                )
                        except Exception:
                            sa = None
                        results.append((desc, sa))
                return results
    except Exception:
        _log.debug("generate assertions from screenshot", exc_info=True)

    return [
        (
            f"Page title contains {page_title or 'the expected page'}",
            StructuredAssertion(
                assertion_type="page_title",
                expected=page_title or "expected page",
                description=f"Page title contains {page_title or 'the expected page'}",
            ),
        )
    ]


async def _get_page_aria_context(page, *, page_state: PageStateCache | None = None) -> str:
    """Return a compact ARIA tree string of the page's interactive elements (max 40 nodes)."""
    try:
        if page_state is not None:
            return await page_state.get_formatted_aria(page, interesting_only=True, max_nodes=40)
        from blop.engine.snapshots import format_snapshot_for_llm

        snapshot = await page.accessibility.snapshot(interesting_only=True)
        if not snapshot:
            return ""
        nodes = extract_interactive_nodes_flat(snapshot, max_nodes=40)
        return format_snapshot_for_llm(nodes)
    except Exception:
        return ""


async def _capture_aria_for_element(
    page, target_text: Optional[str], *, page_state: PageStateCache | None = None
) -> tuple[Optional[str], Optional[str], Optional[str]]:
    """Return (aria_role, aria_name, aria_snapshot_json) for the element matching target_text."""
    if not target_text:
        return None, None, None
    try:
        snapshot = (
            await page_state.get_accessibility_snapshot(page, interesting_only=True)
            if page_state is not None
            else await page.accessibility.snapshot(interesting_only=True)
        )
        if not snapshot:
            return None, None, None
        node = _find_aria_node(snapshot, target_text)
        if node:
            role = node.get("role")
            name = node.get("name")
            # Compact subtree at depth 2
            sub = _serialize_aria_node(node, depth=0, max_depth=2)
            return role, name, json.dumps(sub, separators=(",", ":"))
    except Exception:
        _log.debug("capture ARIA for element", exc_info=True)
    return None, None, None


async def _capture_locator_attrs(
    page, locator: str, action: str, locator_kind: str = "xpath"
) -> tuple[Optional[str], Optional[str], Optional[str], Optional[str]]:
    """Extract data-testid, label text, DOM role, and DOM name for a live DOM element."""
    testid_selector: Optional[str] = None
    label_text: Optional[str] = None
    dom_role: Optional[str] = None
    dom_name: Optional[str] = None
    try:
        result = await page.evaluate(
            """({ locator, locatorKind, testIdAttribute }) => {
                try {
                    let el = null;
                    if (locatorKind === 'xpath') {
                        el = document.evaluate(
                            locator, document, null,
                            XPathResult.FIRST_ORDERED_NODE_TYPE, null
                        ).singleNodeValue;
                    } else {
                        el = document.querySelector(locator);
                    }
                    if (!el) return null;
                    const candidates = Array.from(new Set([
                        testIdAttribute,
                        'data-testid',
                        'data-cy',
                        'data-test',
                    ].filter(Boolean)));
                    let testid = null;
                    let testidAttr = null;
                    for (const attr of candidates) {
                        const value = el.getAttribute(attr);
                        if (value) {
                            testid = value;
                            testidAttr = attr;
                            break;
                        }
                    }
                    let label = null;
                    if (el.getAttribute('aria-label')) {
                        label = el.getAttribute('aria-label');
                    } else if (el.getAttribute('placeholder')) {
                        label = el.getAttribute('placeholder');
                    } else if (el.id) {
                        const lbl = document.querySelector('label[for="' + el.id + '"]');
                        if (lbl) label = lbl.textContent.trim();
                    }
                    // Compute effective ARIA role from tag+type
                    const explicitRole = el.getAttribute('role');
                    const tag = el.tagName.toLowerCase();
                    const TAG_ROLE = {a:'link',button:'button',select:'combobox',textarea:'textbox',h1:'heading',h2:'heading',h3:'heading'};
                    const INPUT_TYPE_ROLE = {checkbox:'checkbox',radio:'radio',button:'button',submit:'button',reset:'button'};
                    let role = explicitRole;
                    if (!role) {
                        if (tag === 'input') role = INPUT_TYPE_ROLE[el.type] || 'textbox';
                        else role = TAG_ROLE[tag] || null;
                    }
                    // Compute accessible name
                    const domName = (
                        el.getAttribute('aria-label') ||
                        el.getAttribute('title') ||
                        (el.textContent||'').trim().slice(0, 80) ||
                        el.getAttribute('placeholder') ||
                        el.value || ''
                    ).trim() || null;
                    return {testid: testid, testidAttr: testidAttr, label: label, role: role, name: domName};
                } catch(e) { return null; }
            }""",
            {"locator": locator, "locatorKind": locator_kind, "testIdAttribute": BLOP_TEST_ID_ATTRIBUTE},
        )
        if result:
            if result.get("testid"):
                testid_selector = _selector_for_test_id(str(result["testid"]), result.get("testidAttr"))
            if action == "fill" and result.get("label"):
                label_text = str(result["label"])[:100]
            dom_role = result.get("role") or None
            dom_name = (result.get("name") or "").strip() or None
    except Exception:
        _log.debug("capture locator attributes (testid, label)", exc_info=True)
    return testid_selector, label_text, dom_role, dom_name


def _extract_interacted_element_hint(interacted) -> dict[str, Optional[str]]:
    """Best-effort semantic hints from browser-use's interacted element object."""
    if interacted is None:
        return {}
    attrs = getattr(interacted, "attributes", None) or {}
    if not isinstance(attrs, dict):
        attrs = {}

    node_name = (getattr(interacted, "node_name", None) or attrs.get("tagName") or "").lower()
    role = attrs.get("role")
    if not role:
        if node_name == "a":
            role = "link"
        elif node_name == "button":
            role = "button"
        elif node_name == "textarea":
            role = "textbox"
        elif node_name == "select":
            role = "combobox"
        elif node_name == "input":
            role = {
                "checkbox": "checkbox",
                "radio": "radio",
                "button": "button",
                "submit": "button",
                "reset": "button",
            }.get((attrs.get("type") or "").lower(), "textbox")

    meaningful_text = None
    try:
        if hasattr(interacted, "get_meaningful_text_for_llm"):
            meaningful_text = interacted.get_meaningful_text_for_llm()
    except Exception:
        _log.debug("extract meaningful interacted text", exc_info=True)

    name = (
        attrs.get("aria-label")
        or attrs.get("title")
        or attrs.get("placeholder")
        or attrs.get("name")
        or meaningful_text
    )
    if isinstance(name, str):
        name = name.strip()[:100] or None
    else:
        name = None

    return {
        "role": role,
        "name": name,
        "target_text": name or (meaningful_text[:100] if isinstance(meaningful_text, str) else None),
        "id": attrs.get("id"),
        "testid": attrs.get("data-testid") or attrs.get("data-cy") or attrs.get("data-test"),
        "placeholder": attrs.get("placeholder"),
        "name_attr": attrs.get("name"),
        "href": attrs.get("href"),
        "input_type": attrs.get("type"),
    }


def _find_aria_node(node: dict, target_text: str) -> Optional[dict]:
    """DFS search for an ARIA node whose name contains target_text (case-insensitive)."""
    name = (node.get("name") or "").lower()
    if target_text.lower() in name:
        return node
    for child in node.get("children", []):
        found = _find_aria_node(child, target_text)
        if found:
            return found
    return None


def _serialize_aria_node(node: dict, depth: int, max_depth: int) -> dict:
    """Serialize an ARIA node tree to a compact dict (bounded depth)."""
    out: dict = {}
    for key in ("role", "name", "value", "checked", "level", "disabled"):
        if node.get(key) is not None:
            out[key] = node[key]
    if depth < max_depth:
        children = [
            _serialize_aria_node(c, depth + 1, max_depth)
            for c in node.get("children", [])
            if c.get("role") not in ("generic", "none", "presentation")
        ]
        if children:
            out["children"] = children
    return out


def _extract_target_text(description: str) -> Optional[str]:
    """Pull the most likely visible label from an action description string."""
    m = re.search(r"['\"](.+?)['\"]", description)
    if m:
        return m.group(1)[:100]
    words = description.split()[:6]
    text = " ".join(words)
    return text[:100] if text else None


def _prefer_semantic_target_text(current: Optional[str], *candidates: Optional[str]) -> Optional[str]:
    """Prefer visible, user-meaningful labels over generic action words."""
    if current and not _is_low_signal_target_text(current):
        return current[:100]
    for candidate in candidates:
        if candidate and not _is_low_signal_target_text(candidate):
            return str(candidate).strip()[:100]
    if current:
        return current[:100]
    return None


def _is_low_signal_target_text(text: Optional[str]) -> bool:
    if not text:
        return True
    normalized = str(text).strip().lower()
    return not normalized or normalized in _LOW_SIGNAL_TARGET_TEXT


def _compute_fingerprint(action: str, selector: Optional[str], target_text: Optional[str], index: int) -> str:
    content = f"{action}|{selector or ''}|{target_text or ''}|{index}"
    return hashlib.md5(content.encode()).hexdigest()[:12]


def _is_brittle_selector(selector: Optional[str]) -> bool:
    """Identify recorder-only selectors that should not drive strict replay."""
    if not selector:
        return False
    sel = selector.strip().lower()
    if "data-browser-use-index" in sel:
        return True
    if sel.startswith("/") or sel.startswith("xpath="):
        # Long absolute XPaths with positional indices are highly unstable across renders.
        if "position()" in sel or "nth" in sel:
            return True
        if sel.count("/") > 7:
            return True
    return False


def _build_replay_recipe(
    *,
    action: str,
    selector: Optional[str],
    target_text: Optional[str],
    testid_selector: Optional[str],
    label_text: Optional[str],
    aria_role: Optional[str],
    aria_name: Optional[str],
) -> list[dict[str, str]]:
    recipe: list[dict[str, str]] = []
    if testid_selector:
        recipe.append({"kind": "testid", "selector": testid_selector})
    if aria_role and aria_name:
        recipe.append({"kind": "role_exact", "role": aria_role, "name": aria_name})
        recipe.append({"kind": "role_fuzzy", "role": aria_role, "name": aria_name})
    if action in ("fill", "upload") and label_text:
        recipe.append({"kind": "label_exact", "text": label_text})
        recipe.append({"kind": "label_fuzzy", "text": label_text})
    if selector:
        recipe.append({"kind": "selector", "selector": selector})
    if target_text:
        recipe.append({"kind": "text_exact", "text": target_text})
        recipe.append({"kind": "text_fuzzy", "text": target_text})

    deduped: list[dict[str, str]] = []
    seen: set[str] = set()
    for candidate in recipe:
        fingerprint = json.dumps(candidate, sort_keys=True)
        if fingerprint in seen:
            continue
        seen.add(fingerprint)
        deduped.append(candidate)
    return deduped


def _map_action(action_name: str) -> Optional[str]:
    name = action_name.lower().replace("_", "")
    # Actions to skip (no browser interaction to replay)
    skip = {
        "done",
        "extractpagecontent",
        "extract",
        "screenshot",
        "saveaspdf",
        "searchpage",
        "findelements",
        "scroll",
        "scrolldown",
        "scrollup",
        "scrolltoelement",
        # browser-use internal actions with no UI replay equivalent
        "goback",
        "writefile",
        "replacefile",
        "opentab",
        "closetab",
        "cacheclickelement",
        "cachetype",
        "cacheextract",
    }
    if name in skip:
        return None
    mapping = {
        "clickelement": "click",
        "click": "click",
        "inputtext": "fill",
        "input": "fill",
        "sendkeys": "fill",
        "navigate": "navigate",
        "gotourl": "navigate",
        "searchgoogle": "navigate",
        "selectdropdownoption": "select",
        "selectoption": "select",
        "uploadfile": "upload",
        "dragdrop": "drag",
        "wait": "wait",
        "switchtab": "navigate",
    }
    for key, val in mapping.items():
        if key in name:
            return val
    return "click"


async def _resolve_spa_entry_url(
    app_url: str,
    storage_state: Optional[str],
    headless: bool,
    goal: str,
) -> Optional[str]:
    """Find the deepest useful entry URL for SPA dashboards.

    Strategy 1: <a href> links pointing to known sub-paths (/editor/, /project/, etc.)
    Strategy 2: data attributes encoding project/item IDs (data-project-id, data-href)
    Strategy 3: Intercept history.pushState before clicking card-like elements and
                capture the URL the SPA navigates to without a full page load.
    """
    from urllib.parse import urljoin

    lease = await BROWSER_POOL.acquire(headless=headless, storage_state=storage_state)
    page = lease.page
    try:
        await page.goto(app_url, wait_until="domcontentloaded", timeout=20000)
        await page.wait_for_timeout(max(BLOP_RECORDING_ENTRY_SETTLE_MS, 0))

        # Strategy 1: anchor links pointing to known deep paths
        sub_path_patterns = [
            "/editor/",
            "/project/",
            "/workspace/",
            "/video/",
            "/canvas/",
            "/document/",
            "/flow/",
        ]
        all_hrefs: list[str] = await page.evaluate("""() =>
            Array.from(document.querySelectorAll('a[href]'))
                .map(a => a.href)
                .filter(h => h && !h.endsWith('#') && !h.endsWith('/'))
        """)
        for href in all_hrefs:
            for pattern in sub_path_patterns:
                if pattern in href:
                    return href

        # Strategy 2: data attributes encoding project/item IDs
        data_link = await page.evaluate("""() => {
            const candidates = document.querySelectorAll(
                '[data-project-id], [data-id], [data-item-id], [data-href]'
            );
            for (const el of candidates) {
                const href = el.dataset.href || el.dataset.projectId || el.dataset.id;
                if (href && href.startsWith('/')) return href;
            }
            return null;
        }""")
        if data_link:
            return urljoin(app_url, data_link)

        # Strategy 3: intercept pushState, click first card-like element
        await page.evaluate("""() => {
            window.__blopNavHistory = [];
            const orig = history.pushState.bind(history);
            history.pushState = function(...args) {
                if (args[2]) window.__blopNavHistory.push(String(args[2]));
                return orig(...args);
            };
        }""")

        initial_url = page.url
        card_selectors = [
            "[data-testid*='project']",
            "[data-testid*='item']",
            "[class*='project'][class*='cursor']",
            "[class*='card'][class*='cursor']",
            "[class*='item'][class*='cursor']",
            ".group.relative.cursor-pointer",
            "[role='gridcell']",
            "[role='listitem']",
        ]
        for sel in card_selectors:
            try:
                el = page.locator(sel).first
                if not await el.count():
                    continue

                href_via_dom: Optional[str] = await page.evaluate(
                    """(selector) => {
                        const el = document.querySelector(selector);
                        if (!el) return null;
                        if (el.tagName === 'A' && el.href) return el.href;
                        const a = el.querySelector('a[href]');
                        if (a && a.href) return a.href;
                        if (el.dataset && el.dataset.href) return el.dataset.href;
                        return null;
                    }""",
                    sel,
                )
                if href_via_dom:
                    for pattern in sub_path_patterns:
                        if pattern in href_via_dom:
                            return href_via_dom

                await el.click(timeout=3000)
                for _ in range(20):
                    await page.wait_for_timeout(200)
                    new_url = page.url
                    if new_url != initial_url and new_url != app_url:
                        return new_url
                    nav_history: list[str] = await page.evaluate("window.__blopNavHistory || []")
                    if nav_history:
                        path = nav_history[-1]
                        if path and path.startswith("/"):
                            return urljoin(app_url, path)
                break
            except Exception:
                continue
    except Exception:
        _log.debug("resolve SPA entry URL (strategy 3)", exc_info=True)
    finally:
        await lease.close()

    return None
