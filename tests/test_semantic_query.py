from __future__ import annotations

import pytest

from blop.engine.semantic_query import evaluate_semantic_query
from blop.schemas import SemanticQuerySpec, StructuredAssertion


class _FakeAccessibility:
    async def snapshot(self, interesting_only: bool = True):
        return {
            "role": "webarea",
            "children": [
                {"role": "button", "name": "Upgrade plan"},
                {"role": "link", "name": "Billing"},
            ],
        }


class _FakeLocator:
    def __init__(self, text: str):
        self._text = text
        self.first = self

    async def text_content(self, timeout: int = 3000):
        return self._text


class _FakePage:
    def __init__(self):
        self.url = "https://example.com/dashboard"
        self.accessibility = _FakeAccessibility()

    def locator(self, selector: str):
        return _FakeLocator("Dashboard ready")

    async def evaluate(self, script: str):
        return "Dashboard ready"

    async def title(self):
        return "Dashboard"


@pytest.mark.asyncio
async def test_semantic_query_uses_accessibility_for_presence_checks():
    page = _FakePage()
    assertion = StructuredAssertion(
        assertion_type="semantic_query",
        description="Upgrade CTA is visible",
        semantic_query=SemanticQuerySpec(
            query="Upgrade CTA is visible",
            target_role="button",
            target_name="Upgrade",
            match_mode="present",
        ),
    )

    result = await evaluate_semantic_query(page, assertion)

    assert result["passed"] is True
    assert result["eval_type"] == "semantic_query_accessibility"


@pytest.mark.asyncio
async def test_semantic_query_uses_dom_text_for_expected_content():
    page = _FakePage()
    assertion = StructuredAssertion(
        assertion_type="semantic_query",
        description="Dashboard message is visible",
        expected="Dashboard",
        target="main",
        semantic_query=SemanticQuerySpec(
            query="Dashboard message is visible",
            expected="Dashboard",
            target_selector="main",
        ),
    )

    result = await evaluate_semantic_query(page, assertion)

    assert result["passed"] is True
    assert result["eval_type"] == "semantic_query_dom"
