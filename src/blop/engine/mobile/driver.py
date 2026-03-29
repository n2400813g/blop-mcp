"""Appium 3 session factory for blop mobile engine.

Requires: pip install blop-mcp[mobile]
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from blop.schemas import MobileDeviceTarget


def _normalize_mobile_provider(raw: str) -> str:
    p = (raw or "local").strip().lower()
    if p in ("local", "browserstack", "lambdatest"):
        return p
    return "local"


def _apply_cloud_hub(options, target: "MobileDeviceTarget") -> str:
    """Set vendor W3C caps on *options*; return the Appium hub URL to connect to."""
    from blop.config import (
        BLOP_APPIUM_URL,
        BLOP_BS_KEY,
        BLOP_BS_USER,
        BLOP_LT_KEY,
        BLOP_LT_USER,
        BLOP_MOBILE_PROVIDER,
    )

    provider = _normalize_mobile_provider(BLOP_MOBILE_PROVIDER)
    if provider == "local":
        return BLOP_APPIUM_URL

    session_label = (target.app_id or "blop")[:48]
    if provider == "browserstack":
        if not BLOP_BS_USER or not BLOP_BS_KEY:
            raise RuntimeError("BLOP_MOBILE_PROVIDER=browserstack requires BLOP_BS_USER and BLOP_BS_KEY")
        options.set_capability(
            "bstack:options",
            {
                "userName": BLOP_BS_USER,
                "accessKey": BLOP_BS_KEY,
                "projectName": "blop-mcp",
                "buildName": "mobile-replay",
                "sessionName": session_label,
            },
        )
        return "https://hub.browserstack.com/wd/hub"

    if provider == "lambdatest":
        if not BLOP_LT_USER or not BLOP_LT_KEY:
            raise RuntimeError("BLOP_MOBILE_PROVIDER=lambdatest requires BLOP_LT_USER and BLOP_LT_KEY")
        options.set_capability(
            "lt:options",
            {
                "username": BLOP_LT_USER,
                "accessKey": BLOP_LT_KEY,
                "project": "blop-mcp",
                "build": "mobile-replay",
                "name": session_label,
                "isRealMobile": True,
            },
        )
        return "https://mobile-hub.lambdatest.com/wd/hub"

    return BLOP_APPIUM_URL


def _get_appium_url() -> str:
    from blop.config import BLOP_APPIUM_URL, BLOP_MOBILE_PROVIDER

    if _normalize_mobile_provider(BLOP_MOBILE_PROVIDER) == "local":
        return BLOP_APPIUM_URL
    if _normalize_mobile_provider(BLOP_MOBILE_PROVIDER) == "browserstack":
        return "https://hub.browserstack.com/wd/hub"
    if _normalize_mobile_provider(BLOP_MOBILE_PROVIDER) == "lambdatest":
        return "https://mobile-hub.lambdatest.com/wd/hub"
    return BLOP_APPIUM_URL


def _require_appium():
    try:
        import appium  # noqa: F401
        from appium import webdriver  # noqa: F401
    except ImportError as exc:
        raise RuntimeError("Mobile testing requires the 'mobile' extra: pip install blop-mcp[mobile]") from exc


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

    from blop.config import BLOP_MOBILE_PROVIDER

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

        url = _apply_cloud_hub(options, target)

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

        url = _apply_cloud_hub(options, target)

    try:
        loop = asyncio.get_running_loop()
        driver = await loop.run_in_executor(None, lambda: webdriver.Remote(url, options=options))
    except Exception as exc:
        prov = _normalize_mobile_provider(BLOP_MOBILE_PROVIDER)
        if prov == "local":
            hint = (
                "Ensure Appium server is running (`appium`) and the target "
                f"{'simulator' if target.platform == 'ios' else 'emulator'} is booted."
            )
        else:
            hint = (
                f"Check BLOP_MOBILE_PROVIDER={prov} credentials and that device_name / os_version "
                "match an available cloud device."
            )
        raise RuntimeError(f"Failed to create Appium session at {url}: {exc}. {hint}") from exc

    return driver


async def check_appium_reachable() -> tuple[bool, str]:
    """Return (reachable, message) for validate_setup."""
    import asyncio
    import urllib.error
    import urllib.request

    from blop.config import (
        BLOP_APPIUM_URL,
        BLOP_BS_KEY,
        BLOP_BS_USER,
        BLOP_LT_KEY,
        BLOP_LT_USER,
        BLOP_MOBILE_PROVIDER,
    )

    prov = _normalize_mobile_provider(BLOP_MOBILE_PROVIDER)
    if prov == "browserstack":
        if BLOP_BS_USER and BLOP_BS_KEY:
            return True, "BrowserStack mobile hub configured (BLOP_BS_USER/BLOP_BS_KEY set)"
        return False, "BLOP_MOBILE_PROVIDER=browserstack but BLOP_BS_USER or BLOP_BS_KEY is missing"
    if prov == "lambdatest":
        if BLOP_LT_USER and BLOP_LT_KEY:
            return True, "LambdaTest mobile hub configured (BLOP_LT_USER/BLOP_LT_KEY set)"
        return False, "BLOP_MOBILE_PROVIDER=lambdatest but BLOP_LT_USER or BLOP_LT_KEY is missing"

    url = BLOP_APPIUM_URL

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
