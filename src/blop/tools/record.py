from __future__ import annotations

import re
from urllib.parse import urlparse
from typing import Optional

from blop.config import validate_app_url
from blop.engine import auth as auth_engine
from blop.engine.flow_builder import build_recorded_flow
from blop.engine.planner import build_execution_plan, build_intent_contract
from blop.engine import recording
from blop.engine.logger import get_logger
from blop.reporting.results import describe_flow_staleness
from blop.schemas import RecordedFlowResult
from blop.storage import sqlite, files as file_store

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
    from blop.schemas import FlowStep
    from blop.engine.recording import _build_public_page_assertions

    entry_url = _select_entry_url(
        next((getattr(step, "value", None) for step in steps if getattr(step, "action", None) == "navigate"), None) or "",
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


async def _find_refresh_candidate(app_url: str, flow_name: str) -> dict | None:
    return await sqlite.find_flow_by_url_and_name(app_url, flow_name)


async def record_test_flow(
    app_url: str,
    flow_name: str,
    goal: str,
    profile_name: Optional[str] = None,
    command: Optional[str] = None,
    business_criticality: str = "other",
    platform: str = "web",
    mobile_target: Optional[dict] = None,
) -> dict:
    # Route mobile flows to the mobile engine
    if platform in ("ios", "android"):
        return await _record_mobile_flow(
            app_id=app_url,
            flow_name=flow_name,
            goal=goal,
            business_criticality=business_criticality,
            platform=platform,
            mobile_target=mobile_target or {},
        )

    url_err = validate_app_url(app_url)
    if url_err:
        return {"error": url_err}
    if not flow_name or not flow_name.strip():
        return {"error": "flow_name is required"}
    if not goal or not goal.strip():
        return {"error": "goal is required"}

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
        except Exception as exc:
            _log.error("auth_resolve_failed profile=%s", profile_name, exc_info=True)
            return {
                "error": (
                    f"Auth profile '{profile_name}' could not be resolved. "
                    "Run capture_auth_session to refresh."
                )
            }
    if storage_state is None:
        storage_state = await auth_engine.auto_storage_state_from_env()

    refresh_candidate = await _find_refresh_candidate(app_url, flow_name)

    import uuid
    run_id = uuid.uuid4().hex

    steps = await recording.record_flow(
        app_url=app_url,
        goal=goal,
        storage_state=storage_state,
        headless=False,
        run_id=run_id,
    )
    steps = _ensure_assertion_steps(steps, goal)
    entry_url = _select_entry_url(app_url, goal, steps)

    # Collect assertion texts from assert steps
    assertions_json = [
        s.value or s.description
        for s in steps
        if s.action == "assert" and (s.value or s.description)
    ]

    valid_criticalities = {"revenue", "activation", "retention", "support", "other"}
    bc = business_criticality if business_criticality in valid_criticalities else "other"

    # Derive spa_hints from the context graph archetype so replay inherits
    # the right wait strategy without manual tuning.
    from blop.engine.context_graph import detect_app_archetype, editor_hints_from_archetype
    from blop.schemas import SpaHints, SiteInventory

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
    if execution_plan.target_surface == "editor" and not any("/editor" in (s.value or "").lower() for s in steps if s.action == "navigate"):
        recording_drift.append("surface_drift")
    if intent_contract.goal_type != "exploration" and not assertions_json:
        recording_drift.append("assertion_drift")
    generic_only_steps = [
        s for s in steps
        if s.action in {"click", "fill"} and not any([s.selector, s.aria_name, s.aria_role, s.target_text, s.testid_selector])
    ]
    if generic_only_steps:
        recording_drift.append("legacy_unstructured")

    flow = build_recorded_flow(
        flow_name=flow_name,
        app_url=app_url,
        goal=goal,
        steps=steps,
        assertions_json=assertions_json,
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
    else:
        result["refresh_summary"] = {"refresh_detected": False}
        result["workflow_hint"] = (
            f"Flow '{flow_name}' recorded ({len(steps)} steps). "
            f"Next: run_release_check(app_url='{app_url}', flow_ids=['{flow.flow_id}'], mode='replay')"
        )
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
        return {"error": "app_url (app_id) is required for mobile flows (use bundle ID or package name)"}
    if not flow_name or not flow_name.strip():
        return {"error": "flow_name is required"}
    if not goal or not goal.strip():
        return {"error": "goal is required"}

    try:
        target = MobileDeviceTarget(platform=platform, app_id=app_id, **mobile_target)
    except Exception as exc:
        return {"error": f"Invalid mobile_target: {exc}"}

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
        return {"error": str(exc)}
    except Exception as exc:
        return {"error": f"Mobile recording failed: {exc}"}

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
            f"Next: run_regression_test(app_url='{app_id}', flow_ids=['{flow.flow_id}'], platform='{platform}')"
        ),
    }
