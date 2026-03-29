from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest


def _assert_ok_envelope(payload: dict) -> dict:
    assert payload["ok"] is True
    assert payload["error"] is None
    assert isinstance(payload["data"], dict)
    return payload["data"]


def _assert_error_message(payload: dict, expected_substring: str) -> None:
    assert "error" in payload
    assert expected_substring.lower() in str(payload["error"]).lower()


@pytest.mark.asyncio
async def test_atomic_snapshot_contract_is_stable():
    from blop.tools.atomic_browser import get_page_snapshot

    with patch(
        "blop.tools.atomic_browser.SESSION_MANAGER.snapshot",
        new=AsyncMock(
            return_value={
                "url": "https://example.com",
                "title": "Example",
                "node_count": 1,
                "snapshot": '- page:\n  - button "Upgrade" [ref=e1]',
                "path": None,
                "snapshot_format": "playwright_mcp_markdown_v1",
                "requested_root_selector": "#pricing",
                "effective_root_selector": "#pricing",
                "root_found": True,
                "test_id_attribute": "data-testid",
                "nodes": [
                    {
                        "ref": "e1",
                        "stable_key": "abc123",
                        "role": "button",
                        "name": "Upgrade",
                        "selector": "[data-testid='upgrade']",
                        "disabled": False,
                    }
                ],
            }
        ),
    ):
        out = await get_page_snapshot(selector="#pricing")

    data = _assert_ok_envelope(out)
    assert data["snapshot_format"] == "playwright_mcp_markdown_v1"
    assert data["requested_root_selector"] == "#pricing"
    assert data["effective_root_selector"] == "#pricing"
    assert data["root_found"] is True
    assert data["nodes"][0]["stable_key"] == "abc123"


@pytest.mark.asyncio
async def test_discover_critical_journeys_invalid_regex_contract():
    from blop.tools.journeys import discover_critical_journeys

    out = await discover_critical_journeys(
        app_url="https://example.com",
        include_url_pattern="(",
    )

    _assert_error_message(out, "Invalid include_url_pattern")


@pytest.mark.asyncio
async def test_triage_release_blocker_requires_identifier_contract():
    from blop.tools.triage import triage_release_blocker

    out = await triage_release_blocker()

    _assert_error_message(out, "At least one of run_id")


@pytest.mark.asyncio
async def test_release_check_request_accepts_smoke_preflight_contract():
    from blop.schemas import ReleaseCheckRequest

    request = ReleaseCheckRequest(app_url="https://example.com", smoke_preflight=True)

    assert request.smoke_preflight is True
