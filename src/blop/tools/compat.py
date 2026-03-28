"""Deprecated compatibility wrappers for the pre-MVP public surface."""

from __future__ import annotations

from typing import Optional

from blop.config import BLOP_ENABLE_COMPAT_TOOLS
from blop.tools import journeys, release_check, resources, validate


def _compat_disabled(tool_name: str, replacement_tool: str) -> dict:
    return {
        "error": (
            f"Deprecated tool '{tool_name}' is disabled. "
            f"Set BLOP_ENABLE_COMPAT_TOOLS=true to use it temporarily, or switch to '{replacement_tool}'."
        ),
        "deprecated": True,
        "replacement_tool": replacement_tool,
    }


def _with_deprecation(
    payload: dict,
    *,
    tool_name: str,
    replacement_tool: str,
    replacement_payload: dict,
) -> dict:
    enriched = dict(payload)
    enriched["deprecated"] = True
    enriched["deprecation_notice"] = {
        "message": f"'{tool_name}' is deprecated; use '{replacement_tool}' instead.",
        "replacement_tool": replacement_tool,
        "replacement_payload": replacement_payload,
        "compat_flag": "BLOP_ENABLE_COMPAT_TOOLS",
    }
    return enriched


async def validate_setup(
    app_url: Optional[str] = None,
    profile_name: Optional[str] = None,
) -> dict:
    """Deprecated compat alias for validate_release_setup."""
    if not BLOP_ENABLE_COMPAT_TOOLS:
        return _compat_disabled("validate_setup", "validate_release_setup")
    result = await validate.validate_release_setup(app_url=app_url, profile_name=profile_name)
    return _with_deprecation(
        result,
        tool_name="validate_setup",
        replacement_tool="validate_release_setup",
        replacement_payload={"app_url": app_url, "profile_name": profile_name},
    )


async def discover_test_flows(
    app_url: str,
    profile_name: Optional[str] = None,
    business_goal: Optional[str] = None,
    max_depth: int = 2,
    max_pages: int = 10,
    seed_urls: Optional[list[str]] = None,
    include_url_pattern: Optional[str] = None,
    exclude_url_pattern: Optional[str] = None,
    return_inventory: bool = False,
) -> dict:
    """Deprecated compat alias for discover_critical_journeys."""
    if not BLOP_ENABLE_COMPAT_TOOLS:
        return _compat_disabled("discover_test_flows", "discover_critical_journeys")
    result = await journeys.discover_critical_journeys(
        app_url=app_url,
        profile_name=profile_name,
        business_goal=business_goal,
        max_depth=max_depth,
        max_pages=max_pages,
        seed_urls=seed_urls,
        include_url_pattern=include_url_pattern,
        exclude_url_pattern=exclude_url_pattern,
    )
    if return_inventory and "inventory" not in result:
        result = dict(result)
        result["inventory"] = None
    return _with_deprecation(
        result,
        tool_name="discover_test_flows",
        replacement_tool="discover_critical_journeys",
        replacement_payload={
            "app_url": app_url,
            "profile_name": profile_name,
            "business_goal": business_goal,
        },
    )


async def run_regression_test(
    app_url: str,
    flow_ids: Optional[list[str]] = None,
    profile_name: Optional[str] = None,
    headless: bool = True,
    run_mode: str = "hybrid",
    release_id: Optional[str] = None,
) -> dict:
    """Deprecated compat alias for run_release_check(mode='replay')."""
    if not BLOP_ENABLE_COMPAT_TOOLS:
        return _compat_disabled("run_regression_test", "run_release_check")
    result = await release_check.run_release_check(
        app_url=app_url,
        flow_ids=flow_ids,
        profile_name=profile_name,
        mode="replay",
        release_id=release_id,
        headless=headless,
        run_mode=run_mode,
    )
    return _with_deprecation(
        result,
        tool_name="run_regression_test",
        replacement_tool="run_release_check",
        replacement_payload={
            "app_url": app_url,
            "flow_ids": flow_ids,
            "profile_name": profile_name,
            "mode": "replay",
        },
    )


async def evaluate_web_task(
    app_url: str,
    task: str,
    profile_name: Optional[str] = None,
    headless: bool = False,
    max_steps: int = 25,
    capture: Optional[list[str]] = None,
    format: str = "markdown",
    save_as_recorded_flow: bool = False,
    flow_name: Optional[str] = None,
) -> dict:
    """Deprecated compat entrypoint; prefers run_release_check(mode='targeted')."""
    if not BLOP_ENABLE_COMPAT_TOOLS:
        return _compat_disabled("evaluate_web_task", "run_release_check")

    # The canonical targeted mode is release-shaped rather than arbitrary-task shaped.
    # Keep the existing behavior for compat callers, but attach the canonical upgrade path.
    from blop.tools.evaluate import evaluate_web_task as legacy_evaluate_web_task

    result = await legacy_evaluate_web_task(
        app_url=app_url,
        task=task,
        profile_name=profile_name,
        headless=headless,
        max_steps=max_steps,
        capture=capture,
        format=format,
        save_as_recorded_flow=save_as_recorded_flow,
        flow_name=flow_name,
    )
    return _with_deprecation(
        result,
        tool_name="evaluate_web_task",
        replacement_tool="run_release_check",
        replacement_payload={
            "app_url": app_url,
            "profile_name": profile_name,
            "mode": "targeted",
        },
    )


async def list_recorded_tests() -> dict:
    """Deprecated compat alias for the blop://journeys resource."""
    if not BLOP_ENABLE_COMPAT_TOOLS:
        return _compat_disabled("list_recorded_tests", "blop://journeys")
    result = await resources.journeys_resource()
    return _with_deprecation(
        result,
        tool_name="list_recorded_tests",
        replacement_tool="blop://journeys",
        replacement_payload={},
    )
