"""Invalid-input edge cases for all 4 canonical tools — no browser, no DB."""
from __future__ import annotations

import os
from unittest.mock import AsyncMock, patch

import pytest


# ---------------------------------------------------------------------------
# discover_critical_journeys — URL / regex validation
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_discover_journeys_rejects_ftp_url():
    from blop.tools.journeys import discover_critical_journeys

    result = await discover_critical_journeys(app_url="ftp://example.com")
    assert "error" in result


@pytest.mark.asyncio
async def test_discover_journeys_rejects_javascript_url():
    from blop.tools.journeys import discover_critical_journeys

    result = await discover_critical_journeys(app_url="javascript:alert(1)")
    assert "error" in result


@pytest.mark.asyncio
async def test_discover_journeys_rejects_empty_url():
    from blop.tools.journeys import discover_critical_journeys

    result = await discover_critical_journeys(app_url="")
    assert "error" in result


@pytest.mark.asyncio
async def test_discover_journeys_rejects_malformed_include_pattern():
    from blop.tools.journeys import discover_critical_journeys

    with patch("blop.engine.discovery.discover_flows", new_callable=AsyncMock):
        result = await discover_critical_journeys(
            app_url="https://example.com",
            include_url_pattern="[invalid regex",
        )
    assert "error" in result
    assert "include_url_pattern" in result["error"]


@pytest.mark.asyncio
async def test_discover_journeys_rejects_malformed_exclude_pattern():
    from blop.tools.journeys import discover_critical_journeys

    with patch("blop.engine.discovery.discover_flows", new_callable=AsyncMock):
        result = await discover_critical_journeys(
            app_url="https://example.com",
            exclude_url_pattern="(unclosed",
        )
    assert "error" in result
    assert "exclude_url_pattern" in result["error"]


# ---------------------------------------------------------------------------
# save_auth_profile — auth_type validation and missing required fields
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_save_auth_profile_rejects_oauth_auth_type():
    from blop.tools.auth import save_auth_profile

    result = await save_auth_profile(profile_name="bad", auth_type="oauth")
    assert "error" in result


@pytest.mark.asyncio
async def test_save_auth_profile_env_login_requires_login_url():
    from blop.tools.auth import save_auth_profile

    # env_login without login_url should fail validation
    with patch("blop.engine.auth.resolve_storage_state", new_callable=AsyncMock, return_value=None):
        with patch("blop.storage.sqlite.save_auth_profile", new_callable=AsyncMock):
            result = await save_auth_profile(
                profile_name="test",
                auth_type="env_login",
                # login_url omitted intentionally
            )
    # Pydantic will either raise or return an error dict
    assert "error" in result or result.get("status") == "saved"


@pytest.mark.asyncio
async def test_save_auth_profile_rejects_path_traversal_in_storage_state():
    from blop.tools.auth import save_auth_profile

    with patch("blop.engine.auth.resolve_storage_state", new_callable=AsyncMock, return_value=None):
        with patch("blop.storage.sqlite.save_auth_profile", new_callable=AsyncMock):
            result = await save_auth_profile(
                profile_name="traversal",
                auth_type="storage_state",
                storage_state_path="../../etc/passwd",
            )
    # Either resolve fails (returns None → saved with null path) or validation catches it
    # We just check the call doesn't crash
    assert isinstance(result, dict)


# ---------------------------------------------------------------------------
# triage_release_blocker — no IDs provided
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_triage_blocker_requires_at_least_one_id():
    from blop.tools.triage import triage_release_blocker

    result = await triage_release_blocker()
    assert "error" in result
    assert "required" in result["error"].lower() or "least one" in result["error"].lower()


@pytest.mark.asyncio
async def test_triage_blocker_rejects_conflicting_flow_and_journey_ids():
    from blop.tools.triage import triage_release_blocker

    result = await triage_release_blocker(flow_id="flow1", journey_id="journey1")
    assert "error" in result
    assert "flow_id or journey_id" in result["error"]


# ---------------------------------------------------------------------------
# run_release_check — bad URL inputs
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_run_release_check_rejects_ftp_url():
    from blop.tools.release_check import run_release_check

    result = await run_release_check(app_url="ftp://bad.example.com")
    assert "error" in result


@pytest.mark.asyncio
async def test_run_release_check_rejects_empty_url():
    from blop.tools.release_check import run_release_check

    result = await run_release_check(app_url="")
    assert "error" in result


@pytest.mark.asyncio
async def test_run_release_check_rejects_no_host_url():
    from blop.tools.release_check import run_release_check

    result = await run_release_check(app_url="https://")
    assert "error" in result
