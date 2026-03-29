from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from blop.config import (
    BLOP_CAPTURE_FAILURE_SCREENSHOTS,
    BLOP_CAPTURE_FINAL_SCREENSHOT,
    BLOP_CAPTURE_NAV_SCREENSHOTS,
    BLOP_CAPTURE_PERIODIC_SCREENSHOTS,
    BLOP_CAPTURE_STEP_SCREENSHOTS,
    BLOP_CAPTURE_TRACE,
    BLOP_CAPTURE_VIDEO,
    BLOP_MAX_EVIDENCE_ARTIFACTS,
    BLOP_MAX_SCREENSHOTS,
    BLOP_SCREENSHOT_INTERVAL_SECS,
)

ScreenshotTrigger = Literal["periodic", "navigation", "step", "failure", "final"]


@dataclass(frozen=True)
class EvidencePolicy:
    trace: bool
    video: bool
    screenshots_enabled: bool
    console: bool
    network: bool
    periodic_screenshots: bool
    navigation_screenshots: bool
    step_screenshots: bool
    failure_screenshots: bool
    final_screenshot: bool
    screenshot_interval_secs: float
    max_screenshots: int
    artifact_cap: int


def resolve_evidence_policy(capture_flags: set[str] | None = None) -> EvidencePolicy:
    requested = set(capture_flags or [])
    screenshot_requested = not requested or "screenshots" in requested
    console_requested = not requested or "console" in requested
    network_requested = not requested or "network" in requested
    trace_requested = "trace" in requested
    return EvidencePolicy(
        trace=BLOP_CAPTURE_TRACE or trace_requested,
        video=BLOP_CAPTURE_VIDEO,
        screenshots_enabled=screenshot_requested,
        console=console_requested,
        network=network_requested,
        periodic_screenshots=screenshot_requested and BLOP_CAPTURE_PERIODIC_SCREENSHOTS,
        navigation_screenshots=screenshot_requested and BLOP_CAPTURE_NAV_SCREENSHOTS,
        step_screenshots=screenshot_requested and BLOP_CAPTURE_STEP_SCREENSHOTS,
        failure_screenshots=screenshot_requested and BLOP_CAPTURE_FAILURE_SCREENSHOTS,
        final_screenshot=screenshot_requested and BLOP_CAPTURE_FINAL_SCREENSHOT,
        screenshot_interval_secs=BLOP_SCREENSHOT_INTERVAL_SECS,
        max_screenshots=BLOP_MAX_SCREENSHOTS,
        artifact_cap=BLOP_MAX_EVIDENCE_ARTIFACTS,
    )


def should_capture_screenshot(policy: EvidencePolicy, trigger: ScreenshotTrigger) -> bool:
    if not policy.screenshots_enabled:
        return False
    if trigger == "periodic":
        return policy.periodic_screenshots
    if trigger == "navigation":
        return policy.navigation_screenshots
    if trigger == "step":
        return policy.step_screenshots
    if trigger == "failure":
        return policy.failure_screenshots
    if trigger == "final":
        return policy.final_screenshot
    return False


def cap_artifact_paths(paths: list[str], *, limit: int | None = None) -> list[str]:
    artifact_limit = max(1, limit or BLOP_MAX_EVIDENCE_ARTIFACTS)
    if len(paths) <= artifact_limit:
        return paths
    return paths[-artifact_limit:]
