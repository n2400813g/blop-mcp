"""Integration test hooks: skip mobile Appium suites when infra is missing."""

from __future__ import annotations

import os
import urllib.error
import urllib.request
from pathlib import Path

import pytest

_INTEGRATION_DIR = Path(__file__).resolve().parent
_TESTS_ROOT = _INTEGRATION_DIR.parent
_APK_PATH = _TESTS_ROOT / "apps" / "mda-android.apk"
_IPA_PATH = _TESTS_ROOT / "apps" / "DVIA-v2.ipa"


def _local_appium_reachable() -> bool:
    prov = os.environ.get("BLOP_MOBILE_PROVIDER", "local").strip().lower()
    if prov in ("browserstack", "lambdatest"):
        return True
    base = os.environ.get("BLOP_APPIUM_URL", "http://127.0.0.1:4723").rstrip("/")
    url = f"{base}/status"
    try:
        with urllib.request.urlopen(url, timeout=3) as resp:
            return resp.status == 200
    except (urllib.error.URLError, OSError, TimeoutError, ValueError):
        return False


def pytest_collection_modifyitems(config, items) -> None:
    if not items:
        return

    appium_ok = _local_appium_reachable()
    skip_appium = pytest.mark.skip(
        reason=(
            "Local Appium not reachable (try: `appium` on default port, drivers uiautomator2/xcuitest, "
            "booted emulator/simulator). Or set BLOP_MOBILE_PROVIDER=browserstack|lambdatest with credentials."
        )
    )

    for item in items:
        fspath = str(getattr(item, "fspath", "") or getattr(item, "path", ""))
        if "test_mobile_sauce_android.py" in fspath:
            if not _APK_PATH.is_file():
                item.add_marker(
                    pytest.mark.skip(reason=f"My Demo App APK missing at {_APK_PATH} (see module docstring).")
                )
            elif not appium_ok:
                item.add_marker(skip_appium)
        elif "test_mobile_dvia_ios.py" in fspath:
            if not _IPA_PATH.is_file():
                item.add_marker(pytest.mark.skip(reason=f"DVIA IPA missing at {_IPA_PATH} (see module docstring)."))
            elif not appium_ok:
                item.add_marker(skip_appium)
