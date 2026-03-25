"""Unit tests for mobile schema extensions (BLO-125).

No device or Appium server required — pure schema + storage tests.
"""
from __future__ import annotations

import json
import pytest

from blop.schemas import (
    FlowStep,
    MobileDeviceTarget,
    MobileEvidenceBundle,
    MobileSelector,
    RecordedFlow,
    FailureCase,
)


# ── MobileSelector ────────────────────────────────────────────────────────────

def test_mobile_selector_accessibility_id():
    sel = MobileSelector(accessibility_id="loginButton")
    assert sel.accessibility_id == "loginButton"
    assert sel.xpath is None


def test_mobile_selector_all_fields():
    sel = MobileSelector(
        accessibility_id="btn",
        predicate_string="label == 'OK'",
        class_chain="**/XCUIElementTypeButton",
        xpath="//XCUIElementTypeButton[@name='OK']",
        android_uiautomator="new UiSelector().text(\"OK\")",
        text="OK",
        content_desc="ok_button",
    )
    dumped = sel.model_dump()
    assert dumped["accessibility_id"] == "btn"
    assert dumped["text"] == "OK"


def test_mobile_selector_empty():
    sel = MobileSelector()
    assert sel.accessibility_id is None
    assert sel.predicate_string is None


# ── MobileDeviceTarget ────────────────────────────────────────────────────────

def test_mobile_device_target_ios_defaults():
    target = MobileDeviceTarget(platform="ios", app_id="com.example.App")
    assert target.platform == "ios"
    assert target.device_name == "iPhone 15"
    assert target.os_version == "17.0"
    assert target.orientation == "portrait"
    assert target.locale == "en_US"
    assert target.device_udid is None


def test_mobile_device_target_android():
    target = MobileDeviceTarget(
        platform="android",
        app_id="com.example.app",
        device_name="Pixel 7",
        os_version="13",
    )
    assert target.platform == "android"
    assert target.device_name == "Pixel 7"


def test_mobile_device_target_roundtrip():
    target = MobileDeviceTarget(platform="ios", app_id="com.example.App", app_version="2.1.0")
    json_str = target.model_dump_json()
    restored = MobileDeviceTarget.model_validate_json(json_str)
    assert restored.app_id == "com.example.App"
    assert restored.app_version == "2.1.0"


# ── MobileEvidenceBundle ──────────────────────────────────────────────────────

def test_mobile_evidence_bundle_defaults():
    bundle = MobileEvidenceBundle(run_id="r1", case_id="c1", platform="ios")
    assert bundle.screenshots == []
    assert bundle.device_log_path is None
    assert bundle.crash_report_path is None


# ── FlowStep mobile actions ───────────────────────────────────────────────────

@pytest.mark.parametrize("action", [
    "tap", "swipe", "long_press", "pinch", "scroll", "back",
    "app_launch", "app_foreground", "app_background",
])
def test_flow_step_mobile_actions_valid(action):
    step = FlowStep(step_id=0, action=action)
    assert step.action == action


def test_flow_step_web_action_unchanged():
    step = FlowStep(step_id=0, action="click", selector="#btn")
    assert step.mobile_selector is None
    assert step.swipe_direction is None
    assert step.touch_x_pct is None


def test_flow_step_mobile_fields():
    sel = MobileSelector(accessibility_id="btn")
    step = FlowStep(
        step_id=1,
        action="tap",
        mobile_selector=sel,
        touch_x_pct=0.5,
        touch_y_pct=0.3,
    )
    assert step.mobile_selector.accessibility_id == "btn"
    assert step.touch_x_pct == 0.5


def test_flow_step_swipe_fields():
    step = FlowStep(
        step_id=2,
        action="swipe",
        swipe_direction="up",
        swipe_distance_pct=0.6,
    )
    assert step.swipe_direction == "up"
    assert step.swipe_distance_pct == 0.6


def test_flow_step_mobile_roundtrip():
    sel = MobileSelector(accessibility_id="loginButton", text="Login")
    step = FlowStep(step_id=0, action="tap", mobile_selector=sel, touch_x_pct=0.5, touch_y_pct=0.8)
    dumped = step.model_dump()
    restored = FlowStep(**dumped)
    assert restored.mobile_selector.accessibility_id == "loginButton"
    assert restored.touch_x_pct == 0.5


# ── RecordedFlow platform fields ──────────────────────────────────────────────

def test_recorded_flow_web_default():
    import datetime
    flow = RecordedFlow(
        flow_name="web flow",
        app_url="https://example.com",
        goal="test",
        steps=[],
        created_at=datetime.datetime.now(datetime.timezone.utc).isoformat(),
    )
    assert flow.platform == "web"
    assert flow.mobile_target is None


def test_recorded_flow_ios():
    import datetime
    target = MobileDeviceTarget(platform="ios", app_id="com.example.App")
    flow = RecordedFlow(
        flow_name="ios login",
        app_url="com.example.App",
        goal="login",
        steps=[FlowStep(step_id=0, action="tap")],
        created_at=datetime.datetime.now(datetime.timezone.utc).isoformat(),
        platform="ios",
        mobile_target=target,
    )
    assert flow.platform == "ios"
    assert flow.mobile_target.app_id == "com.example.App"


# ── FailureCase mobile failure classes ────────────────────────────────────────

@pytest.mark.parametrize("fc", [
    "startup_failure", "install_failure", "navigation_crash",
    "product_bug", "test_fragility", "auth_failure", "env_issue",
])
def test_failure_case_mobile_failure_class(fc):
    case = FailureCase(
        run_id="r1",
        flow_id="f1",
        flow_name="test",
        status="fail",
        failure_class=fc,
    )
    assert case.failure_class == fc


def test_failure_case_mobile_evidence_fields():
    case = FailureCase(
        run_id="r1",
        flow_id="f1",
        flow_name="test",
        status="fail",
        device_log_path="/tmp/syslog.log",
        crash_report_path="/tmp/crash.txt",
        platform="ios",
    )
    assert case.device_log_path == "/tmp/syslog.log"
    assert case.platform == "ios"


def test_failure_case_web_defaults():
    case = FailureCase(
        run_id="r1",
        flow_id="f1",
        flow_name="test",
        status="pass",
    )
    assert case.platform == "web"
    assert case.device_log_path is None


# ── SQLite migration (in-memory) ──────────────────────────────────────────────

@pytest.mark.asyncio
async def test_migration_adds_platform_column(tmp_path, monkeypatch):
    """Migrations 22-27 should apply cleanly to a fresh in-memory-like DB."""
    import os
    db_path = str(tmp_path / "test.db")
    monkeypatch.setenv("BLOP_DB_PATH", db_path)

    from blop.storage.sqlite import init_db
    await init_db()

    import aiosqlite
    async with aiosqlite.connect(db_path) as db:
        async with db.execute("PRAGMA table_info(recorded_flows)") as cur:
            cols = {row[1] for row in await cur.fetchall()}
        assert "platform" in cols
        assert "mobile_target_json" in cols

        async with db.execute("PRAGMA table_info(run_cases)") as cur:
            cols = {row[1] for row in await cur.fetchall()}
        assert "device_log_path" in cols
        assert "crash_report_path" in cols
        assert "platform" in cols

        # Verify mobile_device_sessions table exists
        async with db.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='mobile_device_sessions'"
        ) as cur:
            row = await cur.fetchone()
        assert row is not None


@pytest.mark.asyncio
async def test_save_and_load_mobile_flow(tmp_path, monkeypatch):
    """save_flow + get_flow round-trips a mobile RecordedFlow correctly."""
    import datetime
    db_path = str(tmp_path / "test.db")
    monkeypatch.setenv("BLOP_DB_PATH", db_path)

    from blop.storage.sqlite import init_db, save_flow, get_flow

    await init_db()

    target = MobileDeviceTarget(platform="ios", app_id="com.example.App")
    flow = RecordedFlow(
        flow_name="ios login",
        app_url="com.example.App",
        goal="login flow",
        steps=[FlowStep(step_id=0, action="tap", mobile_selector=MobileSelector(accessibility_id="loginBtn"))],
        created_at=datetime.datetime.now(datetime.timezone.utc).isoformat(),
        platform="ios",
        mobile_target=target,
    )

    await save_flow(flow)
    loaded = await get_flow(flow.flow_id)

    assert loaded is not None
    assert loaded.platform == "ios"
    assert loaded.mobile_target is not None
    assert loaded.mobile_target.app_id == "com.example.App"
    assert len(loaded.steps) == 1
    assert loaded.steps[0].mobile_selector.accessibility_id == "loginBtn"
