from __future__ import annotations

import pytest

from blop.engine.performance import collect_performance_metrics


class _FakePage:
    def __init__(self, result: dict):
        self.result = result
        self.scripts: list[str] = []

    async def evaluate(self, script: str):
        self.scripts.append(script)
        return self.result


@pytest.mark.asyncio
async def test_collect_performance_metrics_returns_richer_vitals():
    page = _FakePage(
        {
            "domContentLoaded": 1200,
            "loadComplete": 1500,
            "domInteractive": 1100,
            "responseEnd": 320,
            "timeToFirstByte": 180,
            "firstPaint": 240,
            "firstContentfulPaint": 300,
            "largestContentfulPaint": 1800,
            "cumulativeLayoutShift": 0.042,
            "lcpEntryCount": 1,
            "clsEntryCount": 2,
            "navigationType": "navigate",
            "transferSize": 4096,
            "encodedBodySize": 2048,
            "decodedBodySize": 8192,
            "redirectCount": 0,
        }
    )

    metrics = await collect_performance_metrics(page)

    assert metrics["largestContentfulPaint"] == 1800
    assert metrics["cumulativeLayoutShift"] == 0.042
    assert metrics["timeToFirstByte"] == 180
    assert metrics["decodedBodySize"] == 8192
    assert "PerformanceObserver" in page.scripts[0]
