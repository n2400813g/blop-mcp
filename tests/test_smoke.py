from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import AsyncMock, patch

import pytest

from blop.schemas import FlowStep, RecordedFlow, SmokeFinding


def _make_flow(flow_id: str, entry_url: str) -> RecordedFlow:
    return RecordedFlow(
        flow_id=flow_id,
        flow_name=flow_id,
        app_url="https://example.com",
        goal="Smoke this area",
        steps=[FlowStep(step_id=0, action="navigate", value=entry_url)],
        created_at=datetime.now(timezone.utc).isoformat(),
        entry_url=entry_url,
    )


@pytest.mark.asyncio
async def test_smoke_preflight_aggregates_findings_and_caps_probe_set():
    from blop.engine.smoke import run_smoke_preflight

    flows = [
        _make_flow("flow-a", "https://example.com/a"),
        _make_flow("flow-b", "https://example.com/b"),
        _make_flow("flow-c", "https://example.com/c"),
        _make_flow("flow-d", "https://example.com/d"),
    ]

    async def _fake_probe(**kwargs):
        return {
            "url": kwargs["url"],
            "final_url": kwargs["url"],
            "interactive_count": 1,
            "findings": [],
        }

    with patch("blop.engine.smoke._probe_url", new=AsyncMock(side_effect=_fake_probe)):
        summary = await run_smoke_preflight(app_url="https://example.com", flows=flows, profile_name=None)

    assert summary.probe_count == 4
    assert "https://example.com" in summary.probed_urls
    assert "https://example.com/d" not in summary.probed_urls


@pytest.mark.asyncio
async def test_smoke_preflight_marks_advisory_findings():
    from blop.engine.smoke import run_smoke_preflight

    flows = [_make_flow("flow-a", "https://example.com/a")]
    fake_finding = {
        "url": "https://example.com",
        "final_url": "https://example.com/login",
        "interactive_count": 0,
        "findings": [SmokeFinding(kind="auth_redirect", message="Redirected", severity="high")],
    }

    with patch("blop.engine.smoke._probe_url", new=AsyncMock(return_value=fake_finding)):
        summary = await run_smoke_preflight(app_url="https://example.com", flows=flows, profile_name=None)

    assert summary.status == "advisory_findings"
    assert summary.findings_by_kind["auth_redirect"] == 2
