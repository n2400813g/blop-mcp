"""Vision fallback for when selectors fail — uses the configured LLM provider."""
from __future__ import annotations

import base64
import json
import os
import re
from typing import TYPE_CHECKING

from blop.engine.logger import get_logger

if TYPE_CHECKING:
    from playwright.async_api import Page

_log = get_logger("vision")


def _llm(max_output_tokens: int = 256):
    from blop.engine.llm_factory import make_planning_llm
    return make_planning_llm(temperature=0.1, max_output_tokens=max_output_tokens)


async def _screenshot_b64(page: "Page") -> str:
    """Capture JPEG screenshot at 70% quality — ~5x smaller than PNG."""
    img_bytes = await page.screenshot(type="jpeg", quality=70)
    return base64.b64encode(img_bytes).decode()


def _check_llm_api_key() -> bool:
    """Return True if at least one supported LLM API key is configured."""
    provider = os.getenv("BLOP_LLM_PROVIDER", "google").lower()
    if provider == "anthropic":
        return bool(os.getenv("ANTHROPIC_API_KEY"))
    if provider == "openai":
        return bool(os.getenv("OPENAI_API_KEY"))
    return bool(os.getenv("GOOGLE_API_KEY"))


def _make_vision_message(text: str, image_b64: str):
    """Create a message with text + image appropriate for the configured provider."""
    provider = os.getenv("BLOP_LLM_PROVIDER", "google").lower()
    if provider in ("anthropic", "openai"):
        from langchain_core.messages import HumanMessage
        return HumanMessage(content=[
            {"type": "text", "text": text},
            {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{image_b64}"}},
        ])
    from browser_use.llm.messages import UserMessage
    return UserMessage(content=[
        {"type": "text", "text": text},
        {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{image_b64}"}},
    ])


async def find_element_coords(page: "Page", description: str) -> tuple[int, int] | None:
    """Take a screenshot and ask the LLM where an element is. Returns (x, y) or None."""
    if not _check_llm_api_key():
        return None

    try:
        b64 = await _screenshot_b64(page)
        size = await page.evaluate("() => ({w: window.innerWidth, h: window.innerHeight})")
        width = size.get("w", 1280)
        height = size.get("h", 800)

        prompt = f"""Look at this screenshot ({width}x{height} pixels).
Find the element described as: "{description}"
Return ONLY a JSON object with the pixel coordinates of its center:
{{"x": <integer>, "y": <integer>}}
If not found, return {{"x": null, "y": null}}"""

        llm = _llm()
        msg = _make_vision_message(prompt, b64)
        response = await llm.ainvoke([msg])
        text = str(response.content) if hasattr(response, "content") else str(response)
        m = re.search(r'\{"x":\s*(\d+|null),\s*"y":\s*(\d+|null)\}', text)
        if m and m.group(1) != "null":
            return int(m.group(1)), int(m.group(2))
        if m and m.group(1) == "null":
            _log.debug(
                "vision_not_found event=vision_not_found description=%s",
                description[:120],
            )
        else:
            _log.debug(
                "vision_parse_failed event=vision_parse_failed mode=find_element_coords description=%s response=%s",
                description[:120],
                text[:200],
            )
    except Exception as e:
        _log.debug(
            "vision_provider_error event=vision_provider_error mode=find_element_coords error_type=%s error_message=%s",
            type(e).__name__,
            str(e)[:200],
            exc_info=True,
        )

    return None


async def click_by_vision(page: "Page", description: str) -> None:
    coords = await find_element_coords(page, description)
    if coords:
        await page.mouse.click(coords[0], coords[1])


async def locate_visual_target(page: "Page", description: str, nearby_text: str | None = None) -> tuple[int, int] | None:
    """Find an element using description plus optional nearby_text hint for disambiguation."""
    full_desc = description
    if nearby_text:
        full_desc = f"{description} (near text: {nearby_text})"
    return await find_element_coords(page, full_desc)


async def assert_by_vision(page: "Page", assertion: str) -> bool:
    """Take a screenshot and ask Gemini whether the assertion is true."""
    results = await assert_all_by_vision(page, [assertion])
    return results[0] if results else False


async def assert_all_by_vision(page: "Page", assertions: list[str]) -> list[bool]:
    """Evaluate multiple assertions in a single LLM call (one screenshot, one call)."""
    if not _check_llm_api_key() or not assertions:
        return [False] * len(assertions)

    try:
        b64 = await _screenshot_b64(page)
        numbered = "\n".join(f'{i + 1}. "{a}"' for i, a in enumerate(assertions))
        prompt = f"""Look at this screenshot. For each assertion, return true or false.

Assertions:
{numbered}

Return ONLY a JSON array of booleans in the same order (e.g. [true, false, true]):"""

        llm = _llm(max_output_tokens=64)
        msg = _make_vision_message(prompt, b64)
        response = await llm.ainvoke([msg])
        text = str(response.content) if hasattr(response, "content") else str(response)
        m = re.search(r"\[.*?\]", text, re.DOTALL)
        if m:
            result = json.loads(m.group())
            if isinstance(result, list):
                padded = list(result) + [False] * (len(assertions) - len(result))
                return [bool(v) for v in padded[:len(assertions)]]
            _log.debug(
                "vision_parse_failed event=vision_parse_failed mode=assert_all_by_vision reason=non_list_json response=%s",
                str(result)[:200],
            )
        else:
            _log.debug(
                "vision_parse_failed event=vision_parse_failed mode=assert_all_by_vision reason=json_array_not_found response=%s",
                text[:200],
            )
    except Exception as e:
        _log.debug(
            "vision_provider_error event=vision_provider_error mode=assert_all_by_vision error_type=%s error_message=%s",
            type(e).__name__,
            str(e)[:200],
            exc_info=True,
        )

    return [False] * len(assertions)
