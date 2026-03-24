"""Mobile evidence capture utilities for blop mobile engine."""
from __future__ import annotations

import asyncio
import base64
import os


async def take_device_screenshot(driver, *, path: str) -> str:
    """Capture a device screenshot and save it to *path*. Returns the path."""
    os.makedirs(os.path.dirname(path), exist_ok=True)
    # driver.get_screenshot_as_base64() is synchronous; run in executor to avoid blocking
    loop = asyncio.get_event_loop()
    b64 = await loop.run_in_executor(None, driver.get_screenshot_as_base64)
    data = base64.b64decode(b64)
    with open(path, "wb") as f:
        f.write(data)
    return path


async def capture_ios_syslog(driver, *, output_path: str) -> None:
    """Write iOS syslog lines captured during the session to *output_path*."""
    os.makedirs(os.path.dirname(output_path), exist_ok=True)
    loop = asyncio.get_event_loop()
    try:
        log_entries = await loop.run_in_executor(None, lambda: driver.get_log("syslog"))
    except Exception:
        log_entries = []

    with open(output_path, "w") as f:
        for entry in log_entries:
            msg = entry.get("message", "") if isinstance(entry, dict) else str(entry)
            f.write(msg + "\n")


async def capture_android_logcat(driver, *, output_path: str) -> None:
    """Write Android logcat lines captured during the session to *output_path*."""
    os.makedirs(os.path.dirname(output_path), exist_ok=True)
    loop = asyncio.get_event_loop()
    try:
        log_entries = await loop.run_in_executor(None, lambda: driver.get_log("logcat"))
    except Exception:
        log_entries = []

    with open(output_path, "w") as f:
        for entry in log_entries:
            msg = entry.get("message", "") if isinstance(entry, dict) else str(entry)
            f.write(msg + "\n")


async def capture_device_logs(driver, *, platform: str, output_path: str) -> None:
    """Dispatch to the correct log capture function for the platform."""
    if platform == "ios":
        await capture_ios_syslog(driver, output_path=output_path)
    else:
        await capture_android_logcat(driver, output_path=output_path)


def read_log_lines(log_path: str) -> list[str]:
    """Read captured log lines from a file. Returns empty list if file absent."""
    try:
        with open(log_path) as f:
            return [line.rstrip("\n") for line in f if line.strip()]
    except FileNotFoundError:
        return []
