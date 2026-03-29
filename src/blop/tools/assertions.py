"""Structured assertion tools — lightweight standalone verifications."""

from __future__ import annotations

from typing import Optional

from blop.engine.browser_runtime import acquire_page_session
from blop.engine.semantic_query import evaluate_semantic_query
from blop.schemas import SemanticQuerySpec, StructuredAssertion


async def _acquire_assertion_session(app_url: str, profile_name: Optional[str]):
    return await acquire_page_session(
        app_url,
        profile_name=profile_name,
        headless=True,
        timeout_ms=30000,
        post_nav_wait_ms=1500,
        allow_auto_env=False,
    )


async def verify_element_visible(
    app_url: str,
    role: str,
    accessible_name: str,
    profile_name: Optional[str] = None,
) -> dict:
    """Navigate to app_url and verify that an ARIA element with the given role/name is visible."""
    session = None
    try:
        session = await _acquire_assertion_session(app_url, profile_name)
        page = session.page
        loc = page.get_by_role(role, name=accessible_name)
        count = await loc.count()
        visible = False
        if count > 0:
            try:
                visible = await loc.first.is_visible()
            except Exception:
                pass
        return {
            "assertion": "element_visible",
            "passed": visible,
            "role": role,
            "accessible_name": accessible_name,
            "app_url": app_url,
            "element_count": count,
        }
    except Exception as e:
        return {"assertion": "element_visible", "passed": False, "error": str(e)}
    finally:
        if session:
            await session.close()


async def verify_text_visible(
    app_url: str,
    text: str,
    profile_name: Optional[str] = None,
) -> dict:
    """Navigate to app_url and verify that the given text is present on the page."""
    session = None
    try:
        session = await _acquire_assertion_session(app_url, profile_name)
        page = session.page
        body_text = await page.inner_text("body")
        found = text in body_text
        return {
            "assertion": "text_visible",
            "passed": found,
            "text": text,
            "app_url": app_url,
        }
    except Exception as e:
        return {"assertion": "text_visible", "passed": False, "error": str(e)}
    finally:
        if session:
            await session.close()


async def verify_value(
    app_url: str,
    selector: str,
    expected_value: str,
    profile_name: Optional[str] = None,
) -> dict:
    """Navigate to app_url and verify that a form field matches the expected value."""
    session = None
    try:
        session = await _acquire_assertion_session(app_url, profile_name)
        page = session.page
        el = page.locator(selector)
        count = await el.count()
        if count == 0:
            return {
                "assertion": "verify_value",
                "passed": False,
                "selector": selector,
                "error": "Element not found",
            }
        actual = await el.first.input_value(timeout=5000)
        passed = actual == expected_value
        return {
            "assertion": "verify_value",
            "passed": passed,
            "selector": selector,
            "expected": expected_value,
            "actual": actual,
            "app_url": app_url,
        }
    except Exception as e:
        return {"assertion": "verify_value", "passed": False, "error": str(e)}
    finally:
        if session:
            await session.close()


async def verify_visual_state(
    app_url: str,
    description: str,
    profile_name: Optional[str] = None,
) -> dict:
    """Navigate to app_url and ask the vision LLM whether a visual condition holds."""
    session = None
    try:
        session = await _acquire_assertion_session(app_url, profile_name)
        page = session.page
        # Lazy import avoids circular dependencies and defers heavy vision/LLM initialization.
        from blop.engine.vision import assert_by_vision

        passed = await assert_by_vision(page, description)
        return {
            "assertion": "visual_state",
            "passed": passed,
            "description": description,
            "app_url": app_url,
        }
    except Exception as e:
        return {"assertion": "visual_state", "passed": False, "error": str(e)}
    finally:
        if session:
            await session.close()


async def verify_semantic_query(
    app_url: str,
    query: str,
    expected: str | None = None,
    profile_name: Optional[str] = None,
    target_selector: str | None = None,
    target_role: str | None = None,
    target_name: str | None = None,
) -> dict:
    """Navigate to app_url and evaluate a semantic query assertion."""
    session = None
    try:
        session = await _acquire_assertion_session(app_url, profile_name)
        assertion = StructuredAssertion(
            assertion_type="semantic_query",
            description=query,
            expected=expected,
            target=target_selector,
            semantic_query=SemanticQuerySpec(
                query=query,
                expected=expected,
                target_selector=target_selector,
                target_role=target_role,
                target_name=target_name,
                match_mode="contains" if expected else "present",
            ),
        )
        result = await evaluate_semantic_query(session.page, assertion)
        result["app_url"] = app_url
        return result
    except Exception as e:
        return {"assertion": query, "passed": False, "error": str(e), "eval_type": "semantic_query"}
    finally:
        if session:
            await session.close()
