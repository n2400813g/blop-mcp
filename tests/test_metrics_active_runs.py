"""Prometheus active-run gauge wiring."""

from __future__ import annotations

import pytest

pytest.importorskip("prometheus_client")

from blop.engine import metrics as blop_metrics


def test_active_runs_gauge_exported():
    blop_metrics.inc_active_run()
    blop_metrics.record_run_terminal(status="completed", duration_seconds=1.0, already_terminal=False)
    text = blop_metrics.metrics_text()
    assert text is not None
    assert "blop_active_runs" in text
