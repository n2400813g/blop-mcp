from __future__ import annotations

from typing import Optional

from blop.config import validate_app_url
from blop.engine import auth as auth_engine
from blop.engine.flow_builder import build_recorded_flow
from blop.engine.planner import build_execution_plan, build_intent_contract
from blop.engine import recording
from blop.engine.logger import get_logger
from blop.schemas import RecordedFlowResult
from blop.storage import sqlite, files as file_store

_log = get_logger("tools.record")


async def record_test_flow(
    app_url: str,
    flow_name: str,
    goal: str,
    profile_name: Optional[str] = None,
    command: Optional[str] = None,
    business_criticality: str = "other",
) -> dict:
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

    import uuid
    run_id = uuid.uuid4().hex

    steps = await recording.record_flow(
        app_url=app_url,
        goal=goal,
        storage_state=storage_state,
        headless=False,
        run_id=run_id,
    )

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
    if execution_plan.goal_type != "exploration" and not assertions_json:
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
        entry_url=app_url,
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
    result["workflow_hint"] = (
        f"Flow '{flow_name}' recorded ({len(steps)} steps). "
        f"Next: run_release_check(app_url='{app_url}', flow_ids=['{flow.flow_id}'], mode='replay')"
    )
    return result
