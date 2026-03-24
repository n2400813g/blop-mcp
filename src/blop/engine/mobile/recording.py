"""Mobile flow recording engine for blop-mcp.

Drives an Appium session via an LLM agent loop and captures each action
as a FlowStep list, mirroring the structure of engine/recording.py.
"""
from __future__ import annotations

import asyncio
import datetime
import json
import uuid
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from blop.schemas import FlowStep, MobileDeviceTarget, RecordedFlow

from blop.storage.files import mobile_screenshot_path


async def record_mobile_flow(
    *,
    app_id: str,
    platform: str,
    goal: str,
    mobile_target: "MobileDeviceTarget",
    run_id: str,
    flow_name: str,
    business_criticality: str = "other",
) -> "RecordedFlow":
    """Record a mobile flow by driving an Appium session with an LLM planning loop.

    Returns a RecordedFlow with platform="ios"|"android" and the captured steps.

    Raises RuntimeError if the Appium client is not installed or session creation fails.
    """
    from blop.engine.mobile.driver import make_appium_driver
    from blop.engine.mobile.evidence import capture_device_logs, take_device_screenshot
    from blop.schemas import FlowStep, MobileSelector, RecordedFlow

    driver = await make_appium_driver(mobile_target)
    steps: list[FlowStep] = []
    step_id = 0

    try:
        # Use the LLM planning loop to drive the session
        # Each action emitted by the planner is translated into a FlowStep
        recorded_actions = await _run_agent_loop(
            driver=driver,
            goal=goal,
            platform=platform,
            run_id=run_id,
            app_id=app_id,
        )

        for action_dict in recorded_actions:
            screenshot_p = mobile_screenshot_path(run_id, "record", step_id, platform)
            try:
                from blop.engine.mobile.evidence import take_device_screenshot as _ss
                await _ss(driver, path=screenshot_p)
            except Exception:
                screenshot_p = None

            mobile_sel = None
            if action_dict.get("accessibility_id") or action_dict.get("text"):
                mobile_sel = MobileSelector(
                    accessibility_id=action_dict.get("accessibility_id"),
                    text=action_dict.get("text"),
                    predicate_string=action_dict.get("predicate_string"),
                    class_chain=action_dict.get("class_chain"),
                    android_uiautomator=action_dict.get("android_uiautomator"),
                )

            step = FlowStep(
                step_id=step_id,
                action=action_dict.get("action", "tap"),
                description=action_dict.get("description", ""),
                value=action_dict.get("value"),
                screenshot_path=screenshot_p,
                mobile_selector=mobile_sel,
                swipe_direction=action_dict.get("swipe_direction"),
                swipe_distance_pct=action_dict.get("swipe_distance_pct"),
                touch_x_pct=action_dict.get("touch_x_pct"),
                touch_y_pct=action_dict.get("touch_y_pct"),
                wait_after_secs=action_dict.get("wait_after_secs", 0.5),
            )
            steps.append(step)
            step_id += 1

    finally:
        # Capture end-of-session logs
        log_p = None
        try:
            from blop.storage.files import device_log_path
            log_p = device_log_path(run_id, "record", platform)
            await capture_device_logs(driver, platform=platform, output_path=log_p)
        except Exception:
            pass
        try:
            driver.quit()
        except Exception:
            pass

    from blop.schemas import RecordedFlow
    return RecordedFlow(
        flow_id=uuid.uuid4().hex,
        flow_name=flow_name,
        app_url=app_id,
        goal=goal,
        steps=steps,
        created_at=datetime.datetime.utcnow().isoformat(),
        business_criticality=business_criticality,  # type: ignore[arg-type]
        platform=platform,  # type: ignore[arg-type]
        mobile_target=mobile_target,
    )


async def _run_agent_loop(
    *,
    driver,
    goal: str,
    platform: str,
    run_id: str,
    app_id: str,
    max_steps: int = 20,
) -> list[dict]:
    """Drive the Appium session via LLM planning to accomplish the goal.

    Returns a list of action dicts describing each step taken.
    This is a simplified planning loop; it uses the existing LLM factory
    and constructs a prompt asking the model what to do next given a
    screenshot and the current accessibility tree.
    """
    from blop.engine.llm_factory import make_planning_llm, make_message

    llm = make_planning_llm()
    actions: list[dict] = []

    system_prompt = (
        f"You are automating a mobile app ({platform}). "
        f"App ID: {app_id}. Goal: {goal}\n"
        "For each step, respond with a JSON object describing the action.\n"
        "Actions: tap, swipe, fill, assert, wait, app_launch, back, scroll.\n"
        "Include: action, description, accessibility_id (if known), text (if known), "
        "swipe_direction (for swipe), value (for fill/assert).\n"
        'Respond with {"done": true} when the goal is achieved or {"error": "reason"} if stuck.'
    )

    for _ in range(max_steps):
        # Capture current screen state
        page_source = ""
        try:
            import asyncio as _asyncio
            loop = _asyncio.get_event_loop()
            page_source = await loop.run_in_executor(None, driver.page_source)
            # Trim to avoid token overflow — keep first 3000 chars
            page_source = page_source[:3000]
        except Exception:
            pass

        history_summary = f"{len(actions)} steps taken so far."
        user_content = (
            f"Current accessibility tree (truncated):\n{page_source}\n\n"
            f"{history_summary}\n"
            "What is the next action? Respond with a single JSON object."
        )

        try:
            response = await make_message(llm, system=system_prompt, user=user_content)
            content = response.content if hasattr(response, "content") else str(response)
            # Parse JSON from response
            start = content.find("{")
            end = content.rfind("}") + 1
            if start == -1:
                break
            action_dict = json.loads(content[start:end])
        except Exception:
            break

        if action_dict.get("done") or action_dict.get("error"):
            break

        # Execute the action
        executed = await _execute_action(driver, action_dict, platform)
        if executed:
            actions.append(action_dict)
            await asyncio.sleep(action_dict.get("wait_after_secs", 0.5))
        else:
            break

    return actions


async def _execute_action(driver, action_dict: dict, platform: str) -> bool:
    """Execute a single action dict against the Appium driver. Returns True on success."""
    from blop.engine.mobile import interaction as intr
    from blop.engine.mobile.appium_selector import find_element
    from blop.schemas import MobileSelector

    action = action_dict.get("action", "")
    loop = asyncio.get_event_loop()

    try:
        if action == "tap":
            sel = MobileSelector(
                accessibility_id=action_dict.get("accessibility_id"),
                text=action_dict.get("text"),
                predicate_string=action_dict.get("predicate_string"),
            )
            if sel.accessibility_id or sel.text or sel.predicate_string:
                element = await find_element(driver, sel, platform)
                await intr.tap(driver, element)
            elif action_dict.get("touch_x_pct") is not None:
                await intr.tap(driver, x_pct=action_dict["touch_x_pct"], y_pct=action_dict["touch_y_pct"])
            return True

        elif action == "swipe":
            await intr.swipe(
                driver,
                action_dict.get("swipe_direction", "up"),
                action_dict.get("swipe_distance_pct", 0.5),
            )
            return True

        elif action == "scroll":
            await intr.scroll(driver, action_dict.get("swipe_direction", "down"))
            return True

        elif action == "fill":
            sel = MobileSelector(
                accessibility_id=action_dict.get("accessibility_id"),
                text=action_dict.get("text"),
            )
            element = await find_element(driver, sel, platform)
            await loop.run_in_executor(None, lambda: element.clear())
            await loop.run_in_executor(None, lambda: element.send_keys(action_dict.get("value", "")))
            return True

        elif action == "back":
            await intr.press_back(driver)
            return True

        elif action == "app_launch":
            await intr.app_launch(driver, action_dict.get("app_id", ""), platform)
            return True

        elif action == "wait":
            await asyncio.sleep(float(action_dict.get("value", 1.0)))
            return True

        elif action == "assert":
            # Non-blocking assertion — just record the intent; verification happens in replay
            return True

    except Exception:
        return False

    return False
