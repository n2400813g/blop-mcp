from __future__ import annotations

from types import SimpleNamespace

import pytest

from blop.engine import recording


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
    }


def test_prefer_semantic_target_text_replaces_generic_action_words():
    assert (
        recording._prefer_semantic_target_text("click", "View Plans", "Upgrade")
        == "View Plans"
    )
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
