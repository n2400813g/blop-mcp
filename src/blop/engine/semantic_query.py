"""Deterministic semantic query evaluation for structured assertions."""

from __future__ import annotations

import re
from typing import TYPE_CHECKING

from blop.engine.dom_utils import extract_interactive_nodes_flat

if TYPE_CHECKING:
    from playwright.async_api import Page

    from blop.schemas import StructuredAssertion


def _normalize_text(value: object) -> str:
    if value is None:
        return ""
    return str(value).strip()


def _matches_expected(observed: str, expected: str | None, match_mode: str) -> bool:
    if match_mode == "present":
        return bool(observed)
    if expected is None:
        return False
    if match_mode == "equals":
        return observed == expected
    if match_mode == "regex":
        try:
            return re.search(expected, observed) is not None
        except re.error:
            return False
    return expected in observed


def _pick_extractor(assertion: "StructuredAssertion") -> str:
    spec = assertion.semantic_query
    if spec is None:
        return "accessible_text"
    if spec.extractor != "auto":
        return spec.extractor
    if spec.target_role or spec.target_name:
        return "interactive_presence"
    if assertion.target == "url":
        return "url"
    if assertion.target == "title":
        return "title"
    return "accessible_text"


async def _extract_accessible_matches(page: "Page", assertion: "StructuredAssertion") -> list[str]:
    spec = assertion.semantic_query
    if spec is None:
        return []
    try:
        snapshot = await page.accessibility.snapshot(interesting_only=True)
    except Exception:
        snapshot = None
    if not snapshot:
        return []
    nodes = extract_interactive_nodes_flat(snapshot, max_nodes=60)
    matches: list[str] = []
    for node in nodes:
        role = _normalize_text(node.get("role"))
        name = _normalize_text(node.get("name"))
        if spec.target_role and role != spec.target_role:
            continue
        if spec.target_name and spec.target_name not in name:
            continue
        observed = " ".join(part for part in (role, name) if part).strip()
        if observed:
            matches.append(observed)
    return matches


async def _extract_dom_text(page: "Page", selector: str | None) -> str:
    try:
        if selector:
            return _normalize_text(await page.locator(selector).first.text_content(timeout=3000))
        return _normalize_text(await page.evaluate("() => document.body.innerText"))
    except Exception:
        return ""


async def evaluate_semantic_query(
    page: "Page",
    assertion: "StructuredAssertion",
    *,
    fallback_to_llm: bool = False,
) -> dict:
    """Evaluate a semantic_query StructuredAssertion and return normalized result data."""
    spec = assertion.semantic_query
    if spec is None:
        raise ValueError("semantic_query assertion requires semantic_query payload")

    extractor = _pick_extractor(assertion)
    observed = ""
    eval_type = "semantic_query"

    if extractor == "interactive_presence":
        matches = await _extract_accessible_matches(page, assertion)
        observed = "\n".join(matches)
        eval_type = "semantic_query_accessibility"
    elif extractor == "url":
        observed = page.url or ""
        eval_type = "semantic_query_url"
    elif extractor == "title":
        try:
            observed = _normalize_text(await page.title())
        except Exception:
            observed = ""
        eval_type = "semantic_query_title"
    else:
        selector = spec.target_selector or assertion.target
        observed = await _extract_dom_text(page, selector)
        if not observed:
            matches = await _extract_accessible_matches(page, assertion)
            observed = "\n".join(matches)
            eval_type = "semantic_query_accessibility_fallback"
        else:
            eval_type = "semantic_query_dom"

    passed = _matches_expected(observed, spec.expected or assertion.expected, spec.match_mode)
    if assertion.negated:
        passed = not passed

    if not passed and fallback_to_llm:
        try:
            from blop.config import check_llm_api_key

            has_key, _ = check_llm_api_key()
            if has_key:
                from blop.engine.vision import assert_by_vision

                passed = await assert_by_vision(page, assertion.description or spec.query)
                eval_type = "semantic_query_llm_fallback"
                if assertion.negated:
                    passed = not passed
        except Exception:
            pass

    return {
        "assertion": assertion.description or spec.query,
        "passed": passed,
        "eval_type": eval_type,
        "observed": observed[:500],
        "query": spec.query,
        "extractor": extractor,
        **({"failed": True} if not passed else {}),
    }
