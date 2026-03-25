"""Unit tests for Appium selector resolution chain (BLO-126).

Uses a mock Appium driver — no device or server required.
The Appium Python client must be installed: pip install blop-mcp[mobile]
"""
from __future__ import annotations

import pytest
from unittest.mock import MagicMock

appium = pytest.importorskip("appium", reason="Appium-Python-Client not installed; skip with pip install blop-mcp[mobile]")

from blop.schemas import MobileSelector
from blop.engine.mobile.appium_selector import describe_selector


# ── describe_selector ─────────────────────────────────────────────────────────

def test_describe_selector_accessibility_id():
    sel = MobileSelector(accessibility_id="loginBtn")
    assert "loginBtn" in describe_selector(sel)
    assert "accessibility_id" in describe_selector(sel)


def test_describe_selector_predicate():
    sel = MobileSelector(predicate_string="label == 'OK'")
    assert "predicate" in describe_selector(sel)


def test_describe_selector_text():
    sel = MobileSelector(text="Submit")
    assert "Submit" in describe_selector(sel)


def test_describe_selector_empty():
    sel = MobileSelector()
    result = describe_selector(sel)
    assert "(empty selector)" in result


# ── find_element selector chain ───────────────────────────────────────────────

class MockElement:
    pass


def _make_mock_driver(success_strategy: str, success_value: str):
    """Return a mock driver that succeeds only for the given (strategy, value) pair."""
    from selenium.common.exceptions import NoSuchElementException

    def find_element(by, value):
        if by == success_strategy and value == success_value:
            return MockElement()
        raise NoSuchElementException(f"No element for {by}={value!r}")

    driver = MagicMock()
    driver.find_element.side_effect = find_element
    return driver


@pytest.mark.asyncio
async def test_find_element_uses_accessibility_id_first():
    from appium.webdriver.common.appiumby import AppiumBy

    driver = _make_mock_driver(AppiumBy.ACCESSIBILITY_ID, "loginBtn")
    sel = MobileSelector(accessibility_id="loginBtn", text="Login")

    from blop.engine.mobile.appium_selector import find_element
    element = await find_element(driver, sel, "ios")
    assert isinstance(element, MockElement)


@pytest.mark.asyncio
async def test_find_element_falls_back_to_predicate():
    from appium.webdriver.common.appiumby import AppiumBy

    # accessibility_id will fail, predicate_string will succeed
    from selenium.common.exceptions import NoSuchElementException

    def find_element(by, value):
        if by == AppiumBy.IOS_PREDICATE and value == "label == 'Login'":
            return MockElement()
        raise NoSuchElementException(f"fail for {by}={value!r}")

    driver = MagicMock()
    driver.find_element.side_effect = find_element

    sel = MobileSelector(accessibility_id="noSuchId", predicate_string="label == 'Login'")

    from blop.engine.mobile.appium_selector import find_element as fe
    element = await fe(driver, sel, "ios")
    assert isinstance(element, MockElement)


@pytest.mark.asyncio
async def test_find_element_raises_when_all_fail():
    from selenium.common.exceptions import NoSuchElementException

    driver = MagicMock()
    driver.find_element.side_effect = NoSuchElementException("not found")

    sel = MobileSelector(accessibility_id="ghost", text="ghost")

    from blop.engine.mobile.appium_selector import find_element
    with pytest.raises(NoSuchElementException):
        await find_element(driver, sel, "ios")


@pytest.mark.asyncio
async def test_find_element_android_uses_uiautomator():
    from appium.webdriver.common.appiumby import AppiumBy
    from selenium.common.exceptions import NoSuchElementException

    ua_str = 'new UiSelector().text("OK")'

    def find_element(by, value):
        if by == AppiumBy.ANDROID_UIAUTOMATOR and value == ua_str:
            return MockElement()
        raise NoSuchElementException(f"fail for {by}={value!r}")

    driver = MagicMock()
    driver.find_element.side_effect = find_element

    sel = MobileSelector(android_uiautomator=ua_str)

    from blop.engine.mobile.appium_selector import find_element as fe
    element = await fe(driver, sel, "android")
    assert isinstance(element, MockElement)
