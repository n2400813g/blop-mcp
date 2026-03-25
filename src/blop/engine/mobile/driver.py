"""Appium 3 session factory for blop mobile engine.

Requires: pip install blop-mcp[mobile]
"""
from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from blop.schemas import MobileDeviceTarget

def _get_appium_url() -> str:
    from blop.config import BLOP_APPIUM_URL
    return BLOP_APPIUM_URL


def _require_appium():
    try:
        import appium  # noqa: F401
        from appium import webdriver  # noqa: F401
    except ImportError as exc:
        raise RuntimeError(
            "Mobile testing requires the 'mobile' extra: pip install blop-mcp[mobile]"
        ) from exc


async def make_appium_driver(target: "MobileDeviceTarget"):
    """Create an Appium 3 session for the given device target.

    Returns an Appium WebDriver instance. The caller is responsible for
    calling driver.quit() when done.

    Raises RuntimeError if the Appium Python client is not installed or
    if the Appium server is not reachable.
    """
    import asyncio

    _require_appium()

    from appium import webdriver

    url = _get_appium_url()

    if target.platform == "ios":
        try:
            from appium.options.ios import XCUITestOptions
        except ImportError:
            from appium.options import AppiumOptions as XCUITestOptions  # type: ignore

        options = XCUITestOptions()
        options.platform_name = "iOS"
        options.automation_name = "XCUITest"
        options.device_name = target.device_name
        options.platform_version = target.os_version
        if target.app_path:
            options.app = target.app_path
        else:
            options.bundle_id = target.app_id
        options.no_reset = True
        options.new_command_timeout = 300
        if target.locale:
            parts = target.locale.split("_", 1)
            options.language = parts[0]
            options.locale = parts[1] if len(parts) > 1 else parts[0]

    else:  # android
        try:
            from appium.options.android import UiAutomator2Options
        except ImportError:
            from appium.options import AppiumOptions as UiAutomator2Options  # type: ignore

        options = UiAutomator2Options()
        options.platform_name = "Android"
        options.automation_name = "UiAutomator2"
        options.device_name = target.device_name
        options.platform_version = target.os_version
        if target.app_path:
            options.app = target.app_path
        else:
            options.app_package = target.app_id
        options.no_reset = True
        options.new_command_timeout = 300
        if target.locale:
            parts = target.locale.split("_", 1)
            options.language = parts[0]
            options.locale = parts[1] if len(parts) > 1 else parts[0]

    try:
        loop = asyncio.get_running_loop()
        driver = await loop.run_in_executor(None, lambda: webdriver.Remote(url, options=options))
    except Exception as exc:
        raise RuntimeError(
            f"Failed to create Appium session at {url}: {exc}. "
            "Ensure Appium server is running (`appium`) and the target "
            f"{'simulator' if target.platform == 'ios' else 'emulator'} is booted."
        ) from exc

    return driver


async def check_appium_reachable() -> tuple[bool, str]:
    """Return (reachable, message) for validate_setup."""
    import asyncio
    import urllib.error
    import urllib.request

    url = _get_appium_url()

    def _check() -> tuple[bool, str]:
        try:
            with urllib.request.urlopen(f"{url}/status", timeout=3) as resp:
                if resp.status == 200:
                    return True, f"Appium server reachable at {url}"
                return False, f"Appium server at {url} returned HTTP {resp.status}"
        except urllib.error.URLError as exc:
            return False, f"Appium server not reachable at {url}: {exc.reason}"
        except Exception as exc:
            return False, f"Appium check failed: {exc}"

    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(None, _check)
