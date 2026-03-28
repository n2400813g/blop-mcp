"""Tests for OTLP-shaped run export."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from blop.reporting.otel_export import build_otel_run_trace_export


@pytest.mark.asyncio
async def test_otel_export_missing_run():
    with patch("blop.reporting.otel_export.sqlite.get_run", new_callable=AsyncMock) as gr:
        gr.return_value = None
        out = await build_otel_run_trace_export("nope")
        assert "error" in out


@pytest.mark.asyncio
async def test_otel_export_structure():
    with patch("blop.reporting.otel_export.sqlite.get_run", new_callable=AsyncMock) as gr:
        gr.return_value = {"run_id": "abc", "app_url": "https://example.com", "status": "completed"}
        with patch("blop.reporting.otel_export.sqlite.list_run_health_events", new_callable=AsyncMock) as lh:
            lh.return_value = [
                {
                    "event_id": "e1",
                    "run_id": "abc",
                    "event_type": "run_started",
                    "payload": {},
                    "created_at": "2025-01-01T00:00:00Z",
                }
            ]
            out = await build_otel_run_trace_export("abc")
    assert "resourceSpans" in out
    assert out["blop_meta"]["span_count"] == 1
