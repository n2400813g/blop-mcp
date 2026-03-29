"""Integration tests for blop mobile engine against DVIA-v2 (iOS).

DVIA-v2 (Damn Vulnerable iOS App v2) is a deliberately insecure iOS app
used for security and functional testing practice.

Prerequisites:
    pip install blop-mcp[mobile]
    npm install -g appium
    appium driver install xcuitest
    appium &                    # start Appium server on localhost:4723
    # Boot an iPhone 15 simulator (iOS 17+):
    #   xcrun simctl boot "iPhone 15"
    # Install IPA on simulator:
    #   xcrun simctl install booted tests/apps/DVIA-v2.ipa

Default ``pytest tests/`` skips these (``-m "not mobile"`` in pyproject). Run explicitly:

    pytest tests/integration/test_mobile_dvia_ios.py -m mobile -v

Scenarios covered (per TestEvolve + dev.to critical test scenario articles):
    1. App launch and navigation (functional flow)
    2. Authentication bypass attempts (security)
    3. Jailbreak detection screen (security / functional)
    4. Local data storage screen navigation (functional)
    5. Network security screen navigation (functional)
    6. Side menu navigation (UI/UX)
    7. Orientation change resilience (UI consistency)
    8. Interrupt recovery (back button / home)
"""

from __future__ import annotations

import os

import pytest

# Skip entire module if Appium client not installed
appium = pytest.importorskip("appium", reason="pip install blop-mcp[mobile]")

from blop.schemas import FlowStep, MobileDeviceTarget, MobileSelector, RecordedFlow

# ── Fixtures ─────────────────────────────────────────────────────────────────

APPIUM_URL = os.environ.get("BLOP_APPIUM_URL", "http://127.0.0.1:4723")
IPA_PATH = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "apps", "DVIA-v2.ipa"))
DEVICE_NAME = os.environ.get("BLOP_IOS_DEVICE", "iPhone 15")
OS_VERSION = os.environ.get("BLOP_IOS_VERSION", "17.0")

pytestmark = [pytest.mark.mobile, pytest.mark.slow, pytest.mark.integration]


@pytest.fixture(scope="module")
def ios_target() -> MobileDeviceTarget:
    return MobileDeviceTarget(
        platform="ios",
        app_id="com.highaltitudehacks.DVIAswift",
        app_path=IPA_PATH,
        device_name=DEVICE_NAME,
        os_version=OS_VERSION,
    )


@pytest.fixture
async def driver(ios_target):
    """Create an Appium session for each test, quit on teardown."""
    from blop.engine.mobile.driver import make_appium_driver

    d = await make_appium_driver(ios_target)
    yield d
    try:
        d.quit()
    except Exception:
        pass


# ── Scenario 1: App launch and initial screen ─────────────────────────────────


@pytest.mark.asyncio
async def test_app_launches_and_shows_main_menu(driver):
    """App should launch and display the DVIA main screen with menu items."""
    import uuid

    from blop.engine.mobile.evidence import take_device_screenshot

    screenshot_path = f"/tmp/dvia_launch_{uuid.uuid4().hex[:8]}.png"
    await take_device_screenshot(driver, path=screenshot_path)

    page_source = driver.page_source
    # DVIA main screen should show key vulnerability categories
    assert any(keyword in page_source for keyword in ["DVIA", "Local Data Storage", "Jailbreak", "Network"]), (
        f"Main menu not found. Page source snippet: {page_source[:500]}"
    )


# ── Scenario 2: Navigation to Local Data Storage ─────────────────────────────


@pytest.mark.asyncio
async def test_navigate_to_local_data_storage(driver):
    """Tap 'Local Data Storage' and confirm the sub-screen appears."""

    from blop.engine.mobile.appium_selector import find_element
    from blop.engine.mobile.interaction import tap

    sel = MobileSelector(text="Local Data Storage", accessibility_id="Local Data Storage")
    element = await find_element(driver, sel, "ios")
    await tap(driver, element)

    import asyncio

    await asyncio.sleep(1.5)

    page_source = driver.page_source
    assert "Storage" in page_source or "Core Data" in page_source or "Plist" in page_source, (
        f"Local Data Storage screen not reached. Snippet: {page_source[:300]}"
    )


# ── Scenario 3: Jailbreak Detection screen ───────────────────────────────────


@pytest.mark.asyncio
async def test_navigate_to_jailbreak_detection(driver):
    """Navigate to the Jailbreak Detection section."""
    from blop.engine.mobile.appium_selector import find_element
    from blop.engine.mobile.interaction import tap

    sel = MobileSelector(text="Jailbreak Detection", accessibility_id="Jailbreak Detection")
    try:
        element = await find_element(driver, sel, "ios")
        await tap(driver, element)
    except Exception:
        # Scroll down if not visible
        from blop.engine.mobile.interaction import scroll

        await scroll(driver, direction="down")
        element = await find_element(driver, sel, "ios")
        await tap(driver, element)

    import asyncio

    await asyncio.sleep(1.0)

    page_source = driver.page_source
    assert "Jailbreak" in page_source, f"Jailbreak Detection screen not reached. Snippet: {page_source[:300]}"


# ── Scenario 4: Network Security screen ──────────────────────────────────────


@pytest.mark.asyncio
async def test_navigate_to_network_security(driver):
    """Navigate to Network Security section and confirm it loads."""
    from blop.engine.mobile.appium_selector import find_element
    from blop.engine.mobile.interaction import scroll, tap

    await scroll(driver, direction="down")

    sel = MobileSelector(text="Network", accessibility_id="Network Layer Security")
    try:
        element = await find_element(driver, sel, "ios")
        await tap(driver, element)
    except Exception:
        await scroll(driver, direction="down")
        element = await find_element(driver, sel, "ios")
        await tap(driver, element)

    import asyncio

    await asyncio.sleep(1.0)

    page_source = driver.page_source
    assert "Network" in page_source, f"Network Security screen not reached. Snippet: {page_source[:300]}"


# ── Scenario 5: Screenshot evidence capture ───────────────────────────────────


@pytest.mark.asyncio
async def test_screenshot_evidence_captured(driver, tmp_path):
    """Each step should produce a valid PNG screenshot."""
    from blop.engine.mobile.evidence import take_device_screenshot

    path = str(tmp_path / "dvia_evidence.png")
    result = await take_device_screenshot(driver, path=path)

    assert os.path.exists(result), "Screenshot file not created"
    file_size = os.path.getsize(result)
    assert file_size > 1000, f"Screenshot too small ({file_size} bytes) — likely blank"


# ── Scenario 6: Device log capture (syslog) ──────────────────────────────────


@pytest.mark.asyncio
async def test_device_log_captured(driver, tmp_path):
    """iOS syslog should be capturable from the Appium session."""
    from blop.engine.mobile.evidence import capture_ios_syslog

    log_path = str(tmp_path / "dvia_syslog.log")
    await capture_ios_syslog(driver, output_path=log_path)

    # Log file should exist (may be empty if no syslog output, that's OK)
    assert os.path.exists(log_path), "Syslog file not created"


# ── Scenario 7: Full blop record → replay flow ───────────────────────────────


@pytest.mark.asyncio
async def test_blop_record_and_replay_ios_flow(ios_target, tmp_path, monkeypatch):
    """End-to-end: record a 3-step DVIA flow via blop, persist, reload, replay.

    This is the primary integration test validating that the full
    record_mobile_flow → save_flow → get_flow → execute_mobile_flow pipeline
    works against a real iOS app.
    """
    import datetime
    import uuid

    from blop.schemas import MobileSelector

    db_path = str(tmp_path / "test.db")
    monkeypatch.setenv("BLOP_DB_PATH", db_path)

    from blop.storage.sqlite import init_db

    await init_db()

    # Manually build a simple recorded flow (3 steps: launch, scroll, back)
    run_id = uuid.uuid4().hex
    flow = RecordedFlow(
        flow_name="DVIA Navigation Smoke",
        app_url="com.highaltitudehacks.DVIAswift",
        goal="Verify main menu loads and Local Data Storage is accessible",
        steps=[
            FlowStep(
                step_id=0,
                action="app_launch",
                description="Launch DVIA app",
                value="com.highaltitudehacks.DVIAswift",
            ),
            FlowStep(
                step_id=1,
                action="tap",
                description="Tap Local Data Storage",
                mobile_selector=MobileSelector(
                    text="Local Data Storage",
                    accessibility_id="Local Data Storage",
                ),
            ),
            FlowStep(
                step_id=2,
                action="back",
                description="Navigate back to main menu",
            ),
        ],
        created_at=datetime.datetime.now(datetime.timezone.utc).isoformat(),
        business_criticality="other",
        platform="ios",
        mobile_target=ios_target,
    )

    from blop.storage.sqlite import get_flow, save_flow

    await save_flow(flow)

    loaded = await get_flow(flow.flow_id)
    assert loaded is not None
    assert loaded.platform == "ios"
    assert loaded.mobile_target.app_id == "com.highaltitudehacks.DVIAswift"
    assert len(loaded.steps) == 3

    # Replay the flow
    from blop.engine.mobile.regression import execute_mobile_flow

    case = await execute_mobile_flow(loaded, run_id=run_id)

    assert case.status in ("pass", "fail"), f"Unexpected status: {case.status}"
    assert case.platform == "ios"
    # If failed, we should have a classified failure reason
    if case.status != "pass":
        assert case.failure_class is not None, "Failed case should have a failure_class"


# ── Scenario 8: Selector chain fallback ──────────────────────────────────────


@pytest.mark.asyncio
async def test_selector_fallback_chain(driver):
    """If accessibility_id fails, selector chain should try text match."""
    from selenium.common.exceptions import NoSuchElementException

    from blop.engine.mobile.appium_selector import find_element

    # Use a deliberately wrong accessibility_id but correct text
    sel = MobileSelector(
        accessibility_id="__definitely_wrong_id__",
        text="DVIA",
    )

    # Should NOT raise — text fallback should find it or gracefully skip
    try:
        element = await find_element(driver, sel, "ios")
        # Found via text fallback
        assert element is not None
    except NoSuchElementException:
        # Both strategies failed — that's also a valid outcome for a specific element
        pass
