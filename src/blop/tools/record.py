from __future__ import annotations

import re
from typing import Annotated, Literal, Optional
from urllib.parse import urlparse

from mcp.server.fastmcp import Context
from pydantic import Field

from blop.engine import auth as auth_engine
from blop.engine import recording
from blop.engine.errors import BLOP_VALIDATION_FAILED, tool_error
from blop.engine.flow_builder import build_recorded_flow
from blop.engine.logger import get_logger
from blop.engine.planner import build_execution_plan, build_intent_contract
from blop.mcp.tool_args import require_coalesced_app_identifier, require_resolved_app_url
from blop.reporting.results import describe_flow_staleness
from blop.schemas import RecordedFlowResult
from blop.storage import files as file_store
from blop.storage import sqlite

_log = get_logger("tools.record")


def _extract_goal_urls(goal: str) -> list[str]:
    matches = re.findall(r"https?://[^\s'\"),]+", goal or "")
    urls: list[str] = []
    for match in matches:
        cleaned = match.rstrip(".,;:")
        if cleaned and cleaned not in urls:
            urls.append(cleaned)
    return urls


def _select_entry_url(app_url: str, goal: str, steps) -> str:
    goal_urls = _extract_goal_urls(goal)
    app_host = (urlparse(app_url).netloc or "").lower()
    for candidate in goal_urls:
        host = (urlparse(candidate).netloc or "").lower()
        if host and host == app_host:
            return candidate
    for step in steps:
        if getattr(step, "action", None) != "navigate":
            continue
        candidate = getattr(step, "value", None) or getattr(step, "url_after", None)
        if not candidate or candidate == app_url:
            continue
        host = (urlparse(candidate).netloc or "").lower()
        if host and host == app_host:
            return candidate
    return app_url


def _ensure_assertion_steps(steps, goal: str):
    has_assert_step = any(getattr(step, "action", None) == "assert" for step in steps)
    if has_assert_step:
        return steps
    from blop.engine.recording import _build_public_page_assertions
    from blop.schemas import FlowStep

    entry_url = _select_entry_url(
        next((getattr(step, "value", None) for step in steps if getattr(step, "action", None) == "navigate"), None)
        or "",
        goal,
        steps,
    )
    synthesized = _build_public_page_assertions(
        goal=goal,
        current_url=entry_url,
        page_title="",
        heading_text=None,
    )
    next_step_id = max((getattr(step, "step_id", 0) for step in steps), default=-1) + 1
    if synthesized:
        for description, structured in synthesized:
            steps.append(
                FlowStep(
                    step_id=next_step_id,
                    action="assert",
                    description=description,
                    value=description,
                    structured_assertion=structured,
                )
            )
            next_step_id += 1
        return steps

    synthesized_assertion = f"Page shows expected content for: {goal}"
    steps.append(
        FlowStep(
            step_id=next_step_id,
            action="assert",
            description=synthesized_assertion,
            value=synthesized_assertion,
        )
    )
    return steps


def _inject_start_url_assert_after_nav(steps: list, app_url: str, goal: str) -> list:
    """Insert a url_contains assert for :func:`recording._recording_start_url` after leading navigates.

    LLM screenshot assertions are often ``semantic`` and replay via vision (flaky). When the goal names a
    deeper same-origin URL, a deterministic URL check should run first so authenticated replay can
    fail clearly on redirects (e.g. expired session) instead of ``vision_batch: unknown``.
    """
    from urllib.parse import urlparse

    from blop.engine.recording import _recording_start_url
    from blop.schemas import FlowStep, StructuredAssertion

    start_url = _recording_start_url(app_url, goal)
    base_p, start_p = urlparse(app_url), urlparse(start_url)

    def _sig(p) -> tuple[str, str]:
        return ((p.path or "").rstrip("/"), p.query or "")

    if _sig(base_p) == _sig(start_p):
        return steps

    expected = start_p.path or "/"
    if start_p.query:
        expected = f"{expected}?{start_p.query}"

    for s in steps:
        if getattr(s, "action", None) != "assert":
            continue
        sa = getattr(s, "structured_assertion", None)
        if sa and sa.assertion_type == "url_contains" and (sa.expected or "") == expected:
            return steps

    nav_end = 0
    while nav_end < len(steps) and steps[nav_end].action == "navigate":
        nav_end += 1

    desc = f"URL contains {expected}"
    insert = FlowStep(
        step_id=nav_end,
        action="assert",
        description=desc,
        value=desc,
        structured_assertion=StructuredAssertion(
            assertion_type="url_contains",
            expected=expected,
            description=desc,
        ),
    )
    out = list(steps[:nav_end]) + [insert] + list(steps[nav_end:])
    return [s.model_copy(update={"step_id": i}) for i, s in enumerate(out)]


async def _find_refresh_candidate(app_url: str, flow_name: str) -> dict | None:
    return await sqlite.find_flow_by_url_and_name(app_url, flow_name)


async def record_test_flow(
    flow_name: str,
    goal: Annotated[
        str,
        Field(
            description="What the flow should accomplish. Used to guide the recording agent.",
            examples=[
                "Complete a purchase with a credit card",
                "Sign up for a new account and verify email confirmation is shown",
            ],
        ),
    ],
    app_url: Optional[str] = None,
    profile_name: Optional[str] = None,
    command: Optional[str] = None,
    business_criticality: Annotated[
        Literal["revenue", "activation", "retention", "support", "other"],
        Field(
            default="other",
            description=(
                "Primary business category for flows. "
                "revenue: checkout/billing/subscriptions. "
                "activation: onboarding/first-run. "
                "retention: core features users return for. "
                "support: help/error recovery. "
                "other: informational or low-stakes flows."
            ),
        ),
    ] = "other",
    platform: str = "web",
    mobile_target: Optional[dict] = None,
    headless: bool = False,
    force_no_auth: bool = False,
    ctx: Context | None = None,
) -> dict:
    if not flow_name or not flow_name.strip():
        return tool_error("flow_name is required", BLOP_VALIDATION_FAILED, details={"field": "flow_name"})
    if not goal or not goal.strip():
        return tool_error("goal is required", BLOP_VALIDATION_FAILED, details={"field": "goal"})

    # Route mobile flows to the mobile engine
    if platform in ("ios", "android"):
        resolved, rid_err = require_coalesced_app_identifier(app_url, field_label="app_url")
        if rid_err:
            return rid_err
        return await _record_mobile_flow(
            app_id=resolved,
            flow_name=flow_name,
            goal=goal,
            business_criticality=business_criticality,
            platform=platform,
            mobile_target=mobile_target or {},
        )

    resolved, url_res_err = require_resolved_app_url(app_url, field_label="app_url")
    if url_res_err:
        return url_res_err
    app_url = resolved

    planning_source = "explicit_goal"
    # If command provided, parse for additional intent context
    if command:
        from blop.engine.planner import parse_command

        intent = await parse_command(command, app_url, profile_name=profile_name)
        if intent.profile_name and not profile_name:
            profile_name = intent.profile_name
        planning_source = "nl_command"

    profile = None
    if profile_name:
        profile = await sqlite.get_auth_profile(profile_name)
        if profile is None:
            return {
                "error": (
                    f"Auth profile '{profile_name}' was not found. "
                    "Provide a valid profile_name, run save_auth_profile, or use capture_auth_session."
                )
            }

    storage_state: Optional[str] = None
    if profile:
        try:
            storage_state = await auth_engine.resolve_storage_state(profile)
        except Exception:
            _log.error("auth_resolve_failed profile=%s", profile_name, exc_info=True)
            return {
                "error": (f"Auth profile '{profile_name}' could not be resolved. Run capture_auth_session to refresh.")
            }
    if storage_state is None and not force_no_auth:
        storage_state = await auth_engine.auto_storage_state_from_env()

    refresh_candidate = await _find_refresh_candidate(app_url, flow_name)

    import uuid

    run_id = uuid.uuid4().hex

    _progress_callback = None
    if ctx is not None:

        async def _progress_callback(current: int, total: int, message: str) -> None:
            try:
                await ctx.report_progress(current, total)
            except Exception:
                pass

    steps = await recording.record_flow(
        app_url=app_url,
        goal=goal,
        storage_state=storage_state,
        headless=headless,
        run_id=run_id,
        progress_callback=_progress_callback,
    )
    steps = _ensure_assertion_steps(steps, goal)
    steps = _inject_start_url_assert_after_nav(steps, app_url, goal)
    entry_url = _select_entry_url(app_url, goal, steps)

    # Collect assertion texts from assert steps
    assertions_json = [s.value or s.description for s in steps if s.action == "assert" and (s.value or s.description)]

    valid_criticalities = {"revenue", "activation", "retention", "support", "other"}
    bc = business_criticality if business_criticality in valid_criticalities else "other"

    # Derive spa_hints from the context graph archetype so replay inherits
    # the right wait strategy without manual tuning.
    from blop.engine.context_graph import detect_app_archetype, editor_hints_from_archetype
    from blop.schemas import SiteInventory, SpaHints

    # Build a minimal inventory from recorded steps to classify the archetype.
    nav_urls = [s.value or "" for s in steps if s.action == "navigate"]
    click_texts = [s.target_text or s.description for s in steps if s.action == "click"]
    _mini_inventory = SiteInventory(
        app_url=app_url,
        routes=list({s.url_before or "" for s in steps if s.url_before} | set(nav_urls)),
        buttons=[{"text": t} for t in click_texts if t],
        links=[],
        forms=[],
        headings=[],
        auth_signals=[],
        business_signals=[],
    )
    archetype = detect_app_archetype(_mini_inventory)
    _hint_kwargs = editor_hints_from_archetype(archetype)
    spa_hints = SpaHints(**_hint_kwargs) if _hint_kwargs else SpaHints()
    run_mode_override = "strict_steps" if _hint_kwargs else None

    execution_plan = build_execution_plan(
        goal_text=goal,
        app_url=app_url,
        command=command,
        profile_name=profile_name,
        business_criticality=bc,
        planning_source=planning_source,
        assertions=assertions_json,
        run_mode=run_mode_override or "hybrid",
    )
    intent_contract = build_intent_contract(execution_plan)

    recording_drift: list[str] = []
    if execution_plan.target_surface == "editor" and not any(
        "/editor" in (s.value or "").lower() for s in steps if s.action == "navigate"
    ):
        recording_drift.append("surface_drift")
    if intent_contract.goal_type != "exploration" and not assertions_json:
        recording_drift.append("assertion_drift")
    generic_only_steps = [
        s
        for s in steps
        if s.action in {"click", "fill"}
        and not any([s.selector, s.aria_name, s.aria_role, s.target_text, s.testid_selector])
    ]
    if generic_only_steps:
        recording_drift.append("legacy_unstructured")

    flow = build_recorded_flow(
        flow_name=flow_name,
        app_url=app_url,
        goal=goal,
        steps=steps,
        assertions_json=assertions_json,
        api_expectations=recording.infer_api_expectations(goal),
        entry_url=entry_url,
        business_criticality=bc,
        spa_hints=spa_hints,
        intent_contract=intent_contract,
        run_mode_override=run_mode_override,
    )
    await sqlite.save_flow(flow)

    artifacts_dir = file_store.artifacts_dir(flow.flow_id)

    result = RecordedFlowResult(
        flow_id=flow.flow_id,
        flow_name=flow_name,
        step_count=len(steps),
        status="recorded",
        artifacts_dir=artifacts_dir,
    ).model_dump()
    result["execution_plan_summary"] = execution_plan.model_dump()
    result["recording_drift"] = {
        "drift_detected": bool(recording_drift),
        "drift_types": recording_drift,
        "assertion_count": len(assertions_json),
        "legacy_unstructured": "legacy_unstructured" in recording_drift,
    }
    if refresh_candidate:
        previous_staleness = describe_flow_staleness(refresh_candidate.get("created_at"))
        result["refresh_summary"] = {
            "refresh_detected": True,
            "previous_flow_id": refresh_candidate.get("flow_id"),
            "previous_created_at": refresh_candidate.get("created_at"),
            "previous_recording_age_days": previous_staleness["age_days"],
            "previous_recording_stale": previous_staleness["stale"],
            "supersedes_previous_recording": True,
        }
        result["workflow_hint"] = (
            f"Flow '{flow_name}' was refreshed and supersedes {refresh_candidate.get('flow_id')}. "
            f"Next: use flow_ids=['{flow.flow_id}'] with run_release_check(app_url='{app_url}', mode='replay')."
        )
        result["workflow"] = {
            "next_action": (
                f"flow refreshed — run replay with run_release_check(app_url='{app_url}', "
                f"flow_ids=['{flow.flow_id}'], mode='replay') or browse flows at blop://journeys"
            )
        }
    else:
        result["refresh_summary"] = {"refresh_detected": False}
        result["workflow_hint"] = (
            f"Flow '{flow_name}' recorded ({len(steps)} steps). "
            f"Next: run_release_check(app_url='{app_url}', flow_ids=['{flow.flow_id}'], mode='replay')"
        )
        result["workflow"] = {
            "next_action": (
                f"flow recorded — run replay with run_release_check(app_url='{app_url}', "
                f"flow_ids=['{flow.flow_id}'], mode='replay') or browse flows at blop://journeys"
            )
        }
    return result


async def _record_mobile_flow(
    *,
    app_id: str,
    flow_name: str,
    goal: str,
    business_criticality: str,
    platform: str,
    mobile_target: dict,
) -> dict:
    """Route to the mobile recording engine."""
    import uuid

    from blop.schemas import MobileDeviceTarget

    if not app_id or not app_id.strip():
        return tool_error(
            "app_url (app_id) is required for mobile flows (use bundle ID or package name)",
            BLOP_VALIDATION_FAILED,
            details={"field": "app_id"},
        )
    if not flow_name or not flow_name.strip():
        return tool_error("flow_name is required", BLOP_VALIDATION_FAILED, details={"field": "flow_name"})
    if not goal or not goal.strip():
        return tool_error("goal is required", BLOP_VALIDATION_FAILED, details={"field": "goal"})

    try:
        target = MobileDeviceTarget(platform=platform, app_id=app_id, **mobile_target)
    except Exception as exc:
        return tool_error(
            f"Invalid mobile_target: {exc}", BLOP_VALIDATION_FAILED, details={"cause": type(exc).__name__}
        )

    run_id = uuid.uuid4().hex

    try:
        from blop.engine.mobile.recording import record_mobile_flow

        flow = await record_mobile_flow(
            app_id=app_id,
            platform=platform,
            goal=goal,
            mobile_target=target,
            run_id=run_id,
            flow_name=flow_name,
            business_criticality=business_criticality,
        )
    except RuntimeError as exc:
        return tool_error(str(exc), BLOP_VALIDATION_FAILED, details={"stage": "mobile_record"})
    except Exception as exc:
        return tool_error(
            f"Mobile recording failed: {exc}",
            BLOP_VALIDATION_FAILED,
            details={"stage": "mobile_record", "cause": type(exc).__name__},
        )

    await sqlite.save_flow(flow)

    return {
        "flow_id": flow.flow_id,
        "flow_name": flow_name,
        "step_count": len(flow.steps),
        "status": "recorded",
        "platform": platform,
        "app_id": app_id,
        "artifacts_dir": file_store.artifacts_dir(flow.flow_id),
        "workflow_hint": (
            f"Mobile flow '{flow_name}' recorded on {platform} ({len(flow.steps)} steps). "
            f"Next: run_regression_test(app_url='{app_id}', flow_ids=['{flow.flow_id}']) "
            f"(install blop-mcp[mobile], Appium running, device/emulator up)."
        ),
    }
