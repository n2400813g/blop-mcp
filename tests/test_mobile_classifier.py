"""Unit tests for mobile failure classifier (BLO-126).

No device or Appium server required.
"""

from __future__ import annotations

from blop.engine.mobile.classifier import classify_mobile_failure


def test_install_failure_from_error():
    fc = classify_mobile_failure(
        error_message="SessionNotCreatedException: failed to create session",
        log_lines=[],
        step_index=0,
    )
    assert fc == "install_failure"


def test_install_failure_from_log():
    fc = classify_mobile_failure(
        error_message="unknown error",
        log_lines=["[error] failed to install app: IPA rejected"],
        step_index=0,
    )
    assert fc == "install_failure"


def test_startup_failure_crash_step_0():
    fc = classify_mobile_failure(
        error_message="element not found",
        log_lines=["FATAL EXCEPTION: main", "signal 11 (SIGSEGV)"],
        step_index=0,
    )
    assert fc == "startup_failure"


def test_startup_failure_crash_step_1():
    fc = classify_mobile_failure(
        error_message="",
        log_lines=["crash in application main thread"],
        step_index=1,
    )
    assert fc == "startup_failure"


def test_navigation_crash_after_launch():
    fc = classify_mobile_failure(
        error_message="element not found",
        log_lines=["[error] fatal exception in com.example.App"],
        step_index=5,
    )
    assert fc == "navigation_crash"


def test_auth_failure_early_steps():
    fc = classify_mobile_failure(
        error_message="assertion failed",
        log_lines=["[warn] session expired", "unauthorized"],
        step_index=2,
    )
    assert fc == "auth_failure"


def test_auth_failure_not_triggered_late():
    # Auth signals after step 3 should not fire the auth rule
    fc = classify_mobile_failure(
        error_message="assertion failed: login required",
        log_lines=[],
        step_index=10,
    )
    # Should not be auth_failure since step_index > 3 and no crash in log
    assert fc is None


def test_env_issue_connection_refused():
    fc = classify_mobile_failure(
        error_message="WebDriverException: connection refused at 127.0.0.1:4723",
        log_lines=[],
        step_index=0,
    )
    assert fc == "env_issue"


def test_no_match_returns_none():
    fc = classify_mobile_failure(
        error_message="element not found: accessibility_id='submitBtn'",
        log_lines=["normal log line", "info: app running"],
        step_index=5,
    )
    assert fc is None


def test_install_takes_priority_over_crash():
    # Installation message should win over crash message
    fc = classify_mobile_failure(
        error_message="could not install app — crash during install",
        log_lines=[],
        step_index=0,
    )
    assert fc == "install_failure"
