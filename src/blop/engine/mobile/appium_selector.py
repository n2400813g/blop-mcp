"""Appium element resolution chain for blop mobile engine.

Priority order:
  1. accessibility_id  (most stable — XCUITest accessibilityIdentifier / UIAutomator2)
  2. predicate_string  (iOS only — NSPredicate)
  3. class_chain       (iOS only — XCUITest class chain)
  4. android_uiautomator (Android only)
  5. content_desc      (Android only)
  6. text              (visible text match, cross-platform)
  7. xpath             (last resort — brittle)
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from blop.schemas import MobileSelector


def _appium_by():
    from appium.webdriver.common.appiumby import AppiumBy

    return AppiumBy


async def find_element(driver, selector: "MobileSelector", platform: str):
    """Resolve and return the first matching element using the selector chain.

    Raises NoSuchElementException (Selenium) if no strategy finds the element.
    """
    By = _appium_by()
    errors = []

    strategies: list[tuple[str, str | None]] = []

    if selector.accessibility_id:
        strategies.append((By.ACCESSIBILITY_ID, selector.accessibility_id))

    if platform == "ios":
        if selector.predicate_string:
            strategies.append((By.IOS_PREDICATE, selector.predicate_string))
        if selector.class_chain:
            strategies.append((By.IOS_CLASS_CHAIN, selector.class_chain))
    elif platform == "android":
        if selector.android_uiautomator:
            strategies.append((By.ANDROID_UIAUTOMATOR, selector.android_uiautomator))
        if selector.content_desc:
            strategies.append((By.ACCESSIBILITY_ID, selector.content_desc))

    if selector.text:
        safe_text = selector.text.replace("'", "\\'")
        strategies.append((By.XPATH, f"//*[@label='{safe_text}' or @value='{safe_text}' or @name='{safe_text}']"))

    if selector.xpath:
        strategies.append((By.XPATH, selector.xpath))

    for strategy, value in strategies:
        if not value:
            continue
        try:
            element = driver.find_element(strategy, value)
            return element
        except Exception as exc:
            errors.append(f"{strategy}={value!r}: {exc}")

    from selenium.common.exceptions import NoSuchElementException

    raise NoSuchElementException(f"No element found after trying {len(strategies)} strategies: " + "; ".join(errors))


def describe_selector(selector: "MobileSelector") -> str:
    """Return a human-readable description of the selector for logging."""
    if selector.accessibility_id:
        return f"accessibility_id={selector.accessibility_id!r}"
    if selector.predicate_string:
        return f"predicate={selector.predicate_string!r}"
    if selector.class_chain:
        return f"class_chain={selector.class_chain!r}"
    if selector.android_uiautomator:
        return f"uiautomator={selector.android_uiautomator!r}"
    if selector.content_desc:
        return f"content_desc={selector.content_desc!r}"
    if selector.text:
        return f"text={selector.text!r}"
    if selector.xpath:
        return f"xpath={selector.xpath!r}"
    return "(empty selector)"
