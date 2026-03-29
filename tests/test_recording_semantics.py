from __future__ import annotations

import sys
from types import SimpleNamespace

import pytest

from blop.engine import recording
from blop.schemas import StructuredAssertion


class _FakePage:
    def __init__(self, result: dict | None):
        self.result = result
        self.calls: list[tuple[str, object]] = []

    async def evaluate(self, script: str, arg: object):
        self.calls.append((script, arg))
        return self.result


@pytest.mark.asyncio
async def test_capture_locator_attrs_supports_css_locators():
    page = _FakePage(
        {
            "testid": "view-plans",
            "label": "View Plans",
            "role": "button",
            "name": "View Plans",
        }
    )

    testid_selector, label_text, dom_role, dom_name = await recording._capture_locator_attrs(
        page,
        "[data-browser-use-index='57']",
        "click",
        locator_kind="css",
    )

    assert testid_selector == "[data-testid='view-plans']"
    assert label_text is None
    assert dom_role == "button"
    assert dom_name == "View Plans"
    assert page.calls[0][1] == {
        "locator": "[data-browser-use-index='57']",
        "locatorKind": "css",
        "testIdAttribute": recording.BLOP_TEST_ID_ATTRIBUTE,
    }


@pytest.mark.asyncio
async def test_capture_locator_attrs_prefers_configured_test_id_attribute(monkeypatch):
    monkeypatch.setattr(recording, "BLOP_TEST_ID_ATTRIBUTE", "data-qa")
    page = _FakePage(
        {
            "testid": "upgrade",
            "testidAttr": "data-qa",
            "label": "Upgrade",
            "role": "button",
            "name": "Upgrade",
        }
    )

    testid_selector, label_text, dom_role, dom_name = await recording._capture_locator_attrs(
        page,
        "#upgrade",
        "click",
        locator_kind="css",
    )

    assert testid_selector == "[data-qa='upgrade']"
    assert label_text is None
    assert dom_role == "button"
    assert dom_name == "Upgrade"


def test_selector_from_interacted_attrs_uses_configured_test_id_attribute(monkeypatch):
    monkeypatch.setattr(recording, "BLOP_TEST_ID_ATTRIBUTE", "data-qa")

    selector = recording._selector_from_interacted_attrs(
        {
            "testid": "upgrade-btn",
            "testid_attr": "data-qa",
        },
        "click",
    )

    assert selector == "[data-qa='upgrade-btn']"


def test_prefer_semantic_target_text_replaces_generic_action_words():
    assert recording._prefer_semantic_target_text("click", "View Plans", "Upgrade") == "View Plans"
    assert recording._prefer_semantic_target_text("Create", "View Plans") == "Create"


def test_extract_interacted_element_hint_uses_attributes_and_infers_role():
    interacted = SimpleNamespace(
        node_name="BUTTON",
        attributes={"aria-label": "Upgrade Plan"},
        get_meaningful_text_for_llm=lambda: "Upgrade Plan",
    )

    hint = recording._extract_interacted_element_hint(interacted)

    assert hint["role"] == "button"
    assert hint["name"] == "Upgrade Plan"
    assert hint["target_text"] == "Upgrade Plan"


@pytest.mark.asyncio
async def test_record_flow_does_not_cancel_missing_screenshot_task(monkeypatch):
    class _FakeHistory:
        def model_actions(self):
            return []

    class _FakeAgent:
        def __init__(self, **kwargs):
            self.kwargs = kwargs

        async def run(self, max_steps: int = 50):
            return _FakeHistory()

    class _FakeBrowserSession:
        def __init__(self, browser_profile=None):
            self.browser_profile = browser_profile
            self.context = SimpleNamespace(pages=[])

        async def aclose(self):
            return None

    fake_browser_use = SimpleNamespace(Agent=_FakeAgent, BrowserSession=_FakeBrowserSession)
    monkeypatch.setitem(sys.modules, "browser_use", fake_browser_use)
    monkeypatch.setattr(recording, "should_capture_screenshot", lambda *args, **kwargs: False)
    monkeypatch.setattr(
        recording, "resolve_evidence_policy", lambda: SimpleNamespace(max_screenshots=0, artifact_cap=0)
    )
    monkeypatch.setattr(
        "blop.engine.browser.make_browser_profile", lambda **kwargs: {"headless": kwargs.get("headless")}
    )
    monkeypatch.setattr("blop.engine.llm_factory.make_agent_llm", lambda **kwargs: object())
    monkeypatch.setattr("blop.engine.llm_factory.make_planning_llm", lambda **kwargs: object())

    steps = await recording.record_flow(
        app_url="https://example.com",
        goal="Verify the homepage loads.",
        storage_state=None,
        headless=True,
        run_id="rec-no-periodic",
    )

    assert steps[0].action == "navigate"
    assert any(step.action == "assert" for step in steps)


def test_infer_api_expectations_for_checkout_goal():
    expectations = recording.infer_api_expectations("Complete checkout and submit payment")

    assert expectations
    assert expectations[0].name == "checkout_api"
    assert expectations[0].required is True


@pytest.mark.asyncio
async def test_generate_assertions_upgrades_semantic_to_semantic_query(monkeypatch):
    class _FakePage:
        url = "https://example.com/dashboard"

        async def title(self):
            return "Dashboard"

        async def inner_text(self, selector: str):
            return "Dashboard"

        async def evaluate(self, script: str):
            return "Dashboard loaded"

        async def screenshot(self, **kwargs):
            return b"fake"

    monkeypatch.setattr("blop.engine.recording._looks_like_public_page_assertion_target", lambda *args, **kwargs: False)
    monkeypatch.setattr("blop.config.check_llm_api_key", lambda: (True, None))

    class _FakeLLM:
        async def ainvoke(self, messages):
            return SimpleNamespace(
                content='[{"type":"semantic","target":"main","expected":"Dashboard","description":"Dashboard state is visible"}]'
            )

    monkeypatch.setattr("blop.engine.llm_factory.make_planning_llm", lambda **kwargs: _FakeLLM())

    assertions = await recording._generate_assertions_from_screenshot(_FakePage(), "Verify dashboard")

    assert assertions
    structured = assertions[0][1]
    assert isinstance(structured, StructuredAssertion)
    assert structured.assertion_type == "semantic_query"
    assert structured.semantic_query is not None
