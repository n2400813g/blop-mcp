"""Performance timing collection — captures web vitals after navigation steps."""
from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from playwright.async_api import Page


async def collect_performance_metrics(page: "Page") -> dict:
    """Collect performance timing metrics from the browser.

    Captures Navigation Timing API data including domContentLoaded, loadComplete,
    and paint timing entries (firstPaint, firstContentfulPaint, largestContentfulPaint).
    """
    try:
        timing = await page.evaluate("""() => {
            const nav = performance.getEntriesByType('navigation')[0] || {};
            const paint = performance.getEntriesByType('paint') || [];
            const lcp = performance.getEntriesByType('largest-contentful-paint') || [];

            const paintMap = {};
            for (const p of paint) {
                paintMap[p.name] = Math.round(p.startTime);
            }

            return {
                domContentLoaded: Math.round(nav.domContentLoadedEventEnd || 0),
                loadComplete: Math.round(nav.loadEventEnd || 0),
                domInteractive: Math.round(nav.domInteractive || 0),
                responseEnd: Math.round(nav.responseEnd || 0),
                firstPaint: paintMap['first-paint'] || null,
                firstContentfulPaint: paintMap['first-contentful-paint'] || null,
                largestContentfulPaint: lcp.length > 0 ? Math.round(lcp[lcp.length - 1].startTime) : null,
                transferSize: Math.round(nav.transferSize || 0),
                encodedBodySize: Math.round(nav.encodedBodySize || 0),
            };
        }""")
        return timing if isinstance(timing, dict) else {}
    except Exception:
        return {}


def is_slow_page(metrics: dict, threshold_ms: int = 5000) -> bool:
    """Return True if any key metric exceeds the threshold."""
    lcp = metrics.get("largestContentfulPaint")
    load = metrics.get("loadComplete")
    if lcp and lcp > threshold_ms:
        return True
    if load and load > threshold_ms:
        return True
    return False
