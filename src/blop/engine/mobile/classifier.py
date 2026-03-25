"""Deterministic mobile failure classification for blop mobile engine.

Rules fire before LLM classification. If no rule matches, the caller
should fall back to engine/classifier.py classify_case() with platform context.
"""
from __future__ import annotations

_CRASH_SIGNALS = [
    "fatal exception",
    "anr",
    "application not responding",
    "signal 11",
    "sigsegv",
    "crash",
    "exception in thread",
    "java.lang.runtimeexception",
    "xcode.crash",
    "exc_bad_access",
    "terminated due to",
]

_INSTALL_SIGNALS = [
    "sessionnotcreatedexception",
    "failed to install",
    "could not install",
    "installation failed",
    "unable to launch",
    "failed to launch",
    "app did not start",
    "appium.exception.webdriveragentrunner",
]

_AUTH_SIGNALS = [
    "login required",
    "authentication failed",
    "session expired",
    "unauthorized",
    "sign in to continue",
]


def _matches(log_lines: list[str], signals: list[str]) -> bool:
    combined = " ".join(log_lines).lower()
    return any(s in combined for s in signals)


def classify_mobile_failure(
    *,
    error_message: str,
    log_lines: list[str],
    step_index: int,
) -> str | None:
    """Return a mobile failure_class string or None if no deterministic rule fires.

    Args:
        error_message: The exception/error string from the failed step.
        log_lines: Lines from the device log (syslog/logcat) captured during the run.
        step_index: 0-based index of the step that failed.

    Returns:
        One of "startup_failure", "install_failure", "navigation_crash",
        "auth_failure", "env_issue", or None.
    """
    error_lower = (error_message or "").lower()
    all_lines = log_lines + [error_lower]

    # Install / session creation failure
    if _matches(all_lines, _INSTALL_SIGNALS) or "sessionnotcreated" in error_lower:
        return "install_failure"

    # Startup failure (crash in first 2 steps)
    if step_index <= 1 and _matches(all_lines, _CRASH_SIGNALS):
        return "startup_failure"

    # Navigation crash (crash after app launched successfully)
    if _matches(all_lines, _CRASH_SIGNALS):
        return "navigation_crash"

    # Auth failure
    if step_index <= 3 and _matches(all_lines, _AUTH_SIGNALS):
        return "auth_failure"

    # Appium server / environment issue (avoid matching normal assertion/wait timeouts)
    if any(s in error_lower for s in ["connection refused", "webdriverexception", "connection timed out", "appium server timed out", "could not connect"]):
        return "env_issue"

    return None
