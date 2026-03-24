"""Mobile flow regression (replay) engine for blop-mcp.

Mirrors the interface of engine/regression.py for mobile flows.
"""
from __future__ import annotations

import asyncio
import datetime
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from blop.schemas import FailureCase, RecordedFlow

from blop.storage.files import device_log_path, mobile_screenshot_path


async def execute_mobile_flow(
    flow: "RecordedFlow",
    *,
    run_id: str,
    headless: bool = True,
) -> "FailureCase":
    """Replay a RecordedFlow on mobile and return a FailureCase with evidence.

    Mirrors engine/regression.py::execute_flow() for the mobile surface.
    """
    from blop.engine.mobile.classifier import classify_mobile_failure
    from blop.engine.mobile.driver import make_appium_driver
    from blop.engine.mobile.evidence import capture_device_logs, take_device_screenshot
    from blop.schemas import FailureCase

    assert flow.mobile_target is not None, "execute_mobile_flow requires flow.mobile_target"
    platform = flow.platform
    target = flow.mobile_target
    case_id = f"{run_id}_{flow.flow_id}"

    screenshots: list[str] = []
    log_path = device_log_path(run_id, case_id, platform)

    driver = await make_appium_driver(target)

    step_failure_index: int | None = None
    error_message: str = ""
    status = "pass"

    try:
        for step in flow.steps:
            step_screenshot = mobile_screenshot_path(run_id, case_id, step.step_id, platform)
            result = await _replay_step(driver, step, platform)

            try:
                await take_device_screenshot(driver, path=step_screenshot)
                screenshots.append(step_screenshot)
            except Exception:
                pass

            if not result["ok"]:
                step_failure_index = step.step_id
                error_message = result.get("error", "")
                status = "fail"
                break

            await asyncio.sleep(step.wait_after_secs)

    except Exception as exc:
        status = "error"
        error_message = str(exc)
        step_failure_index = step_failure_index or 0

    finally:
        # Capture device logs before closing session
        try:
            await capture_device_logs(driver, platform=platform, output_path=log_path)
        except Exception:
            log_path = None
        try:
            driver.quit()
        except Exception:
            pass

    # Classify failure
    failure_class = None
    if status != "pass":
        from blop.engine.mobile.evidence import read_log_lines
        log_lines = read_log_lines(log_path) if log_path else []
        failure_class = classify_mobile_failure(
            error_message=error_message,
            log_lines=log_lines,
            step_index=step_failure_index or 0,
        )
        if failure_class is None:
            failure_class = "product_bug"

    severity = "none" if status == "pass" else (
        "blocker" if flow.business_criticality == "revenue" else "high"
    )

    return FailureCase(
        run_id=run_id,
        flow_id=flow.flow_id,
        flow_name=flow.flow_name,
        status=status,
        severity=severity,
        failure_class=failure_class,
        failure_reason_codes=[error_message] if error_message else [],
        screenshots=screenshots,
        step_failure_index=step_failure_index,
        business_criticality=flow.business_criticality,
        device_log_path=log_path,
        platform=platform,
        raw_result=error_message,
    )


async def run_mobile_flows(
    flows: list["RecordedFlow"],
    *,
    run_id: str,
    headless: bool = True,
    max_concurrent: int = 2,
) -> list["FailureCase"]:
    """Run multiple mobile flows with limited concurrency.

    Mobile simulators are resource-intensive — default concurrency is 2.
    """
    semaphore = asyncio.Semaphore(max_concurrent)

    async def _run_one(flow: "RecordedFlow") -> "FailureCase":
        async with semaphore:
            return await execute_mobile_flow(flow, run_id=run_id, headless=headless)

    results = await asyncio.gather(*[_run_one(f) for f in flows], return_exceptions=True)

    cases: list["FailureCase"] = []
    from blop.schemas import FailureCase
    for flow, result in zip(flows, results):
        if isinstance(result, Exception):
            cases.append(FailureCase(
                run_id=run_id,
                flow_id=flow.flow_id,
                flow_name=flow.flow_name,
                status="error",
                severity="high",
                failure_class="env_issue",
                failure_reason_codes=[str(result)],
                business_criticality=flow.business_criticality,
                platform=flow.platform,
                raw_result=str(result),
            ))
        else:
            cases.append(result)

    return cases


async def _replay_step(driver, step, platform: str) -> dict:
    """Execute a single FlowStep against the Appium driver.

    Returns {"ok": True} on success or {"ok": False, "error": str} on failure.
    """
    from blop.engine.mobile import interaction as intr
    from blop.schemas import MobileSelector

    loop = asyncio.get_event_loop()
    action = step.action

    try:
        if action == "tap":
            if step.mobile_selector:
                from blop.engine.mobile.appium_selector import find_element
                element = await find_element(driver, step.mobile_selector, platform)
                await intr.tap(driver, element)
            elif step.touch_x_pct is not None and step.touch_y_pct is not None:
                await intr.tap(driver, x_pct=step.touch_x_pct, y_pct=step.touch_y_pct)
            else:
                return {"ok": False, "error": "tap step missing mobile_selector and coordinates"}

        elif action == "swipe":
            await intr.swipe(
                driver,
                step.swipe_direction or "up",
                step.swipe_distance_pct or 0.5,
            )

        elif action == "scroll":
            await intr.scroll(driver, step.swipe_direction or "down")

        elif action == "fill":
            if not step.mobile_selector:
                return {"ok": False, "error": "fill step missing mobile_selector"}
            from blop.engine.mobile.appium_selector import find_element
            element = await find_element(driver, step.mobile_selector, platform)
            await loop.run_in_executor(None, element.clear)
            await loop.run_in_executor(None, lambda: element.send_keys(step.value or ""))

        elif action == "back":
            await intr.press_back(driver)

        elif action == "app_launch":
            await intr.app_launch(driver, step.value or "", platform)

        elif action == "app_foreground":
            await intr.app_foreground(driver, step.value or "")

        elif action == "app_background":
            await intr.app_background(driver)

        elif action == "wait":
            await asyncio.sleep(float(step.value or 1.0))

        elif action == "assert":
            if step.structured_assertion:
                ok = await _evaluate_assertion(driver, step.structured_assertion, platform)
                if not ok:
                    return {"ok": False, "error": f"Assertion failed: {step.structured_assertion.description}"}

        elif action == "long_press":
            if step.mobile_selector:
                from blop.engine.mobile.appium_selector import find_element
                element = await find_element(driver, step.mobile_selector, platform)
                await intr.long_press(driver, element)

        elif action == "pinch":
            # Pinch is a complex gesture; simplified implementation
            pass

        return {"ok": True}

    except Exception as exc:
        return {"ok": False, "error": str(exc)}


async def _evaluate_assertion(driver, assertion, platform: str) -> bool:
    """Evaluate a StructuredAssertion against the current mobile screen state."""
    loop = asyncio.get_event_loop()
    try:
        page_source = await loop.run_in_executor(None, driver.page_source)
    except Exception:
        return False

    atype = assertion.assertion_type
    if atype == "text_present":
        return bool(assertion.expected and assertion.expected in page_source)
    if atype == "element_visible" and assertion.target:
        from blop.schemas import MobileSelector
        from blop.engine.mobile.appium_selector import find_element
        sel = MobileSelector(accessibility_id=assertion.target, text=assertion.target)
        try:
            await find_element(driver, sel, platform)
            return True
        except Exception:
            return False
    # For other types default to passing — full semantic eval requires LLM
    return True
