"""Visual regression engine — pixel diff + LLM vision triage."""

from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import TYPE_CHECKING

from blop.config import BLOP_ALLOW_SCREENSHOT_LLM

if TYPE_CHECKING:
    from playwright.async_api import Page


VISUAL_DIFF_THRESHOLD = float(os.getenv("BLOP_VISUAL_DIFF_THRESHOLD", "0.02"))
_log = logging.getLogger(__name__)


async def save_golden_screenshot(page: "Page", flow_id: str, step_index: int) -> str:
    """Capture and save a golden baseline screenshot during recording."""
    from blop.storage.files import baseline_path

    path = baseline_path(flow_id, step_index)
    await page.screenshot(path=path, full_page=False, type="png")
    return path


def get_golden_path(flow_id: str, step_index: int) -> str | None:
    """Return the golden baseline path if it exists."""
    from blop.storage.files import baseline_path

    p = baseline_path(flow_id, step_index)
    return p if Path(p).exists() else None


def pixel_diff(baseline_path: str, current_path: str) -> tuple[float, str | None, bool]:
    """Compare two screenshots using Pillow and return (diff_ratio, diff_image_path, size_mismatch).

    diff_ratio is 0.0 (identical) to 1.0 (completely different).
    diff_image_path is a PNG highlighting changed pixels.
    size_mismatch is True when screenshots have different dimensions.

    Raises:
        Exception: Propagates Pillow/file errors so callers can treat failures
            differently from valid "identical screenshot" results.
    """
    try:
        from PIL import Image, ImageChops

        baseline = Image.open(baseline_path).convert("RGB")
        current = Image.open(current_path).convert("RGB")

        if baseline.size != current.size:
            return 1.0, None, True

        diff = ImageChops.difference(baseline, current)
        grayscale = diff.convert("L")
        total = grayscale.size[0] * grayscale.size[1]
        if total == 0:
            return 0.0, None, False

        histogram = grayscale.histogram()
        changed = total - sum(histogram[:31])
        ratio = changed / total

        diff_path = current_path.replace(".png", "_diff.png")
        diff.save(diff_path)
        return round(ratio, 6), diff_path, False
    except Exception:
        raise


async def compare_screenshots(
    page: "Page",
    flow_id: str,
    step_index: int,
    current_screenshot_path: str | None = None,
) -> dict:
    """Compare current page state against golden baseline for a step.

    Returns dict with keys: has_baseline, diff_ratio, diff_path, is_regression,
    vision_triage (only if diff exceeds threshold).
    """
    golden = get_golden_path(flow_id, step_index)
    if not golden:
        return {"has_baseline": False, "diff_ratio": 0.0, "is_regression": False}

    if not current_screenshot_path:
        from blop.storage.files import baseline_dir

        current_screenshot_path = str(baseline_dir(flow_id) / f"step_{step_index:03d}_current.png")
        await page.screenshot(path=current_screenshot_path, full_page=False, type="png")

    diff_ratio, diff_path, size_mismatch = pixel_diff(golden, current_screenshot_path)

    result: dict = {
        "has_baseline": True,
        "diff_ratio": diff_ratio,
        "diff_path": diff_path,
        "size_mismatch": size_mismatch,
        "is_regression": False,
    }

    threshold = float(os.getenv("BLOP_VISUAL_DIFF_THRESHOLD", str(VISUAL_DIFF_THRESHOLD)))
    if size_mismatch:
        # Size mismatch is always considered a meaningful visual regression signal.
        result["is_regression"] = True
    elif diff_ratio > threshold:
        triage = await _vision_triage(golden, current_screenshot_path, diff_ratio)
        result["vision_triage"] = triage
        result["is_regression"] = triage.get("is_meaningful", True)

    return result


async def _vision_triage(baseline_path: str, current_path: str, diff_ratio: float) -> dict:
    """Ask the vision LLM whether the visual difference is a real regression or benign."""
    import base64

    from blop.engine.vision import _check_llm_api_key, _llm

    if not BLOP_ALLOW_SCREENSHOT_LLM:
        _log.info("visual triage skipped: BLOP_ALLOW_SCREENSHOT_LLM is disabled")
        return {"is_meaningful": True, "reason": "llm_triage_disabled_by_config"}

    if not _check_llm_api_key():
        return {"is_meaningful": True, "reason": "no_llm_key"}

    try:
        with open(baseline_path, "rb") as f:
            baseline_b64 = base64.b64encode(f.read()).decode()
        with open(current_path, "rb") as f:
            current_b64 = base64.b64encode(f.read()).decode()

        from blop.engine.secrets import mask_text

        prompt = (
            f"Compare these two screenshots. The pixel diff ratio is {diff_ratio:.4f}.\n"
            "The first image is the BASELINE (expected), the second is the CURRENT state.\n\n"
            "Is this a meaningful visual regression or a benign change (timestamp, dynamic content, "
            "animation frame, random avatar, etc.)?\n\n"
            "Return ONLY a JSON object: "
            '{"is_meaningful": true/false, "reason": "<brief explanation>"}'
        )
        prompt = mask_text(prompt)

        llm = _llm(max_output_tokens=128)

        provider = os.getenv("BLOP_LLM_PROVIDER", "google").lower()
        if provider in ("anthropic", "openai"):
            from langchain_core.messages import HumanMessage

            msg = HumanMessage(
                content=[
                    {"type": "text", "text": prompt},
                    {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{baseline_b64}"}},
                    {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{current_b64}"}},
                ]
            )
        else:
            from browser_use.llm.messages import UserMessage

            msg = UserMessage(
                content=[
                    {"type": "text", "text": prompt},
                    {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{baseline_b64}"}},
                    {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{current_b64}"}},
                ]
            )

        import json
        import re

        response = await llm.ainvoke([msg])
        text = str(response.content) if hasattr(response, "content") else str(response)
        m = re.search(r"\{.*\}", text, re.DOTALL)
        if m:
            return json.loads(m.group())
    except Exception:
        pass

    return {"is_meaningful": True, "reason": "triage_failed"}


async def compare_visual_baseline(flow_id: str, step_index: int | None = None) -> dict:
    """On-demand visual comparison without an open browser — compares saved screenshots.

    When a baseline and a corresponding ``_current.png`` screenshot exist for the
    same step, pixel_diff is run and vision triage is invoked when the diff exceeds
    the configured threshold.
    """
    from blop.storage.files import baseline_dir

    bdir = baseline_dir(flow_id)
    if not bdir.exists():
        return {"error": f"No baselines found for flow {flow_id}"}

    if step_index is not None:
        golden = get_golden_path(flow_id, step_index)
        if not golden:
            return {"error": f"No baseline for step {step_index}"}

        current_path = str(bdir / f"step_{step_index:03d}_current.png")
        if Path(current_path).exists():
            diff_ratio, diff_path, size_mismatch = pixel_diff(golden, current_path)
            result: dict = {
                "flow_id": flow_id,
                "step_index": step_index,
                "baseline_path": golden,
                "current_path": current_path,
                "has_baseline": True,
                "diff_ratio": diff_ratio,
                "diff_path": diff_path,
                "size_mismatch": size_mismatch,
                "is_regression": False,
            }
            threshold = float(os.getenv("BLOP_VISUAL_DIFF_THRESHOLD", str(VISUAL_DIFF_THRESHOLD)))
            if size_mismatch:
                result["is_regression"] = True
            elif diff_ratio > threshold:
                triage = await _vision_triage(golden, current_path, diff_ratio)
                result["vision_triage"] = triage
                result["is_regression"] = triage.get("is_meaningful", True)
            return result

        return {
            "flow_id": flow_id,
            "step_index": step_index,
            "baseline_path": golden,
            "has_baseline": True,
            "has_current": False,
        }

    baselines = sorted(bdir.glob("step_*.png"))
    baseline_only = [b for b in baselines if "_current" not in b.name and "_diff" not in b.name]
    comparisons: list[dict] = []
    for b in baseline_only:
        stem = b.stem
        current_candidate = bdir / f"{stem}_current.png"
        entry: dict = {"baseline_path": str(b), "step": stem}
        if current_candidate.exists():
            diff_ratio, diff_path, size_mismatch = pixel_diff(str(b), str(current_candidate))
            entry["current_path"] = str(current_candidate)
            entry["diff_ratio"] = diff_ratio
            entry["diff_path"] = diff_path
            entry["size_mismatch"] = size_mismatch
            threshold = float(os.getenv("BLOP_VISUAL_DIFF_THRESHOLD", str(VISUAL_DIFF_THRESHOLD)))
            entry["is_regression"] = False
            if size_mismatch:
                entry["is_regression"] = True
            elif diff_ratio > threshold:
                fallback_is_regression = diff_ratio > threshold
                try:
                    triage = await _vision_triage(str(b), str(current_candidate), diff_ratio)
                    entry["vision_triage"] = triage
                    triage_is_meaningful = triage.get("is_meaningful")
                    triage_failed = triage.get("reason") == "triage_failed"
                    if isinstance(triage_is_meaningful, bool) and not triage_failed:
                        entry["is_regression"] = triage_is_meaningful
                    else:
                        entry["is_regression"] = fallback_is_regression
                except Exception:
                    entry["is_regression"] = fallback_is_regression
        comparisons.append(entry)

    return {
        "flow_id": flow_id,
        "baseline_count": len(baseline_only),
        "comparisons": comparisons,
    }
