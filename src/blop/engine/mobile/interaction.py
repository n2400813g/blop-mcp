"""Mobile interaction helpers for blop mobile engine (Appium 3 / W3C Actions)."""

from __future__ import annotations

import asyncio


def _run_sync(fn, *args, **kwargs):
    loop = asyncio.get_event_loop()
    return loop.run_in_executor(None, lambda: fn(*args, **kwargs))


async def tap(driver, element=None, *, x_pct: float | None = None, y_pct: float | None = None) -> None:
    """Tap an element or an absolute coordinate (as fraction of screen size)."""
    if element is not None:
        await _run_sync(element.click)
        return

    if x_pct is not None and y_pct is not None:
        size = await _run_sync(driver.get_window_size)
        x = int(size["width"] * x_pct)
        y = int(size["height"] * y_pct)
        actions = _get_action_chains(driver)
        await _run_sync(lambda: actions.tap(None, x, y).perform())
        return

    raise ValueError("tap() requires either element or (x_pct, y_pct)")


async def swipe(
    driver,
    direction: str,  # up | down | left | right
    distance_pct: float = 0.5,
) -> None:
    """Swipe in the given direction by distance_pct of the screen dimension."""
    size = await _run_sync(driver.get_window_size)
    w, h = size["width"], size["height"]

    cx, cy = w // 2, h // 2

    if direction == "up":
        start_x, start_y = cx, int(cy + h * distance_pct / 2)
        end_x, end_y = cx, int(cy - h * distance_pct / 2)
    elif direction == "down":
        start_x, start_y = cx, int(cy - h * distance_pct / 2)
        end_x, end_y = cx, int(cy + h * distance_pct / 2)
    elif direction == "left":
        start_x, start_y = int(cx + w * distance_pct / 2), cy
        end_x, end_y = int(cx - w * distance_pct / 2), cy
    elif direction == "right":
        start_x, start_y = int(cx - w * distance_pct / 2), cy
        end_x, end_y = int(cx + w * distance_pct / 2), cy
    else:
        raise ValueError(f"Unknown swipe direction: {direction!r}")

    await _run_sync(driver.swipe, start_x, start_y, end_x, end_y, 500)


async def long_press(driver, element) -> None:
    """Long-press an element."""
    actions = _get_action_chains(driver)
    await _run_sync(lambda: actions.long_press(element).release().perform())


async def scroll(driver, direction: str = "down", distance_pct: float = 0.4) -> None:
    """Scroll the screen in the given direction."""
    await swipe(driver, direction, distance_pct)


async def press_back(driver) -> None:
    """Press the Android back button (no-op on iOS)."""
    await _run_sync(driver.back)


async def app_launch(driver, app_id: str, platform: str) -> None:
    """Activate (bring to foreground) or launch the app.

    Appium 3's activate_app works uniformly on both iOS and Android.
    """
    await _run_sync(driver.activate_app, app_id)


async def app_background(driver) -> None:
    """Send the app to the background."""
    await _run_sync(driver.background_app, 3)


async def app_foreground(driver, app_id: str) -> None:
    """Bring the app back to the foreground."""
    await _run_sync(driver.activate_app, app_id)


def _get_action_chains(driver):
    """Return a W3C ActionChains instance (Selenium 4 / Appium 3)."""
    from selenium.webdriver.common.action_chains import ActionChains

    return ActionChains(driver)
