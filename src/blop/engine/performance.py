"""Performance timing collection — captures web vitals after navigation steps."""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from playwright.async_api import Page


async def collect_performance_metrics(page: "Page") -> dict:
    """Collect performance timing metrics from the browser.

    Captures Navigation Timing API data including domContentLoaded, loadComplete,
    paint timing entries, and richer vitals such as LCP and CLS when available.
    """
    try:
        # Give buffered observers a short window to flush post-load paint entries.
        await asyncio.sleep(0.25)
        timing = await page.evaluate("""async () => {
            if (!window.__blopPerfObserverInstalled) {
                window.__blopPerfObserverInstalled = true;
                window.__blopPerfVitals = window.__blopPerfVitals || { lcp: null, cls: 0, lcpEntries: 0, clsEntries: 0 };
                try {
                    const lcpObserver = new PerformanceObserver((entryList) => {
                        for (const entry of entryList.getEntries()) {
                            window.__blopPerfVitals.lcp = Math.round(entry.startTime || 0);
                            window.__blopPerfVitals.lcpEntries += 1;
                        }
                    });
                    lcpObserver.observe({ type: 'largest-contentful-paint', buffered: true });
                } catch (e) {}
                try {
                    const clsObserver = new PerformanceObserver((entryList) => {
                        for (const entry of entryList.getEntries()) {
                            if (!entry.hadRecentInput) {
                                window.__blopPerfVitals.cls += entry.value || 0;
                                window.__blopPerfVitals.clsEntries += 1;
                            }
                        }
                    });
                    clsObserver.observe({ type: 'layout-shift', buffered: true });
                } catch (e) {}
            }

            await new Promise((resolve) => setTimeout(resolve, 250));
            const nav = performance.getEntriesByType('navigation')[0] || {};
            const paint = performance.getEntriesByType('paint') || [];
            const lcp = performance.getEntriesByType('largest-contentful-paint') || [];
            const vitals = window.__blopPerfVitals || { lcp: null, cls: 0, lcpEntries: 0, clsEntries: 0 };

            const paintMap = {};
            for (const p of paint) {
                paintMap[p.name] = Math.round(p.startTime);
            }

            return {
                domContentLoaded: Math.round(nav.domContentLoadedEventEnd || 0),
                loadComplete: Math.round(nav.loadEventEnd || 0),
                domInteractive: Math.round(nav.domInteractive || 0),
                responseEnd: Math.round(nav.responseEnd || 0),
                timeToFirstByte: Math.round((nav.responseStart || 0) - (nav.startTime || 0)),
                firstPaint: paintMap['first-paint'] || null,
                firstContentfulPaint: paintMap['first-contentful-paint'] || null,
                largestContentfulPaint: vitals.lcp || (lcp.length > 0 ? Math.round(lcp[lcp.length - 1].startTime) : null),
                cumulativeLayoutShift: Math.round((vitals.cls || 0) * 1000) / 1000,
                lcpEntryCount: Math.round(vitals.lcpEntries || 0),
                clsEntryCount: Math.round(vitals.clsEntries || 0),
                navigationType: nav.type || null,
                transferSize: Math.round(nav.transferSize || 0),
                encodedBodySize: Math.round(nav.encodedBodySize || 0),
                decodedBodySize: Math.round(nav.decodedBodySize || 0),
                redirectCount: Math.round(nav.redirectCount || 0),
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
