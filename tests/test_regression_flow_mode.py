"""Tests for tools.regression mobile vs web run splitting."""

from __future__ import annotations

from datetime import datetime, timezone

from blop.schemas import FlowStep, MobileDeviceTarget, RecordedFlow
from blop.tools.regression import _regression_flow_mode


def _web_flow(flow_id: str = "w1") -> RecordedFlow:
    return RecordedFlow(
        flow_id=flow_id,
        flow_name="web",
        app_url="https://example.com",
        goal="g",
        steps=[FlowStep(step_id=0, action="navigate", value="https://example.com")],
        created_at=datetime.now(timezone.utc).isoformat(),
        platform="web",
    )


def _android_flow(flow_id: str = "m1") -> RecordedFlow:
    return RecordedFlow(
        flow_id=flow_id,
        flow_name="mob",
        app_url="com.example.app",
        goal="g",
        steps=[FlowStep(step_id=0, action="tap", mobile_selector=None, touch_x_pct=0.5, touch_y_pct=0.5)],
        created_at=datetime.now(timezone.utc).isoformat(),
        platform="android",
        mobile_target=MobileDeviceTarget(
            platform="android",
            app_id="com.example.app",
            device_name="Pixel 6",
            os_version="13.0",
        ),
    )


def test_regression_flow_mode_web_only():
    mode, err = _regression_flow_mode([_web_flow()])
    assert mode == "web"
    assert err is None


def test_regression_flow_mode_mobile_only():
    mode, err = _regression_flow_mode([_android_flow()])
    assert mode == "mobile"
    assert err is None


def test_regression_flow_mode_mixed_rejected():
    mode, err = _regression_flow_mode([_web_flow(), _android_flow()])
    assert mode == "error"
    assert err and "mix" in err.lower()


def test_regression_flow_mode_mobile_missing_target():
    bad = _android_flow()
    bad.mobile_target = None
    mode, err = _regression_flow_mode([bad])
    assert mode == "error"
    assert err and "mobile_target" in err
