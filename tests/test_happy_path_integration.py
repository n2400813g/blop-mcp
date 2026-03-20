"""End-to-end happy-path tests against https://practicesoftwaretesting.com.

Marks: happy_path, slow, integration.

Skip conditions:
  - GOOGLE_API_KEY not set
  - Network not reachable (BLOP_SKIP_NETWORK_TESTS=1)

Credentials (all have sane defaults for the practice site):
  TEST_URL        default: https://practicesoftwaretesting.com
  TEST_USERNAME   default: customer@practicesoftwaretesting.com
  TEST_PASSWORD   default: welcome01
"""
from __future__ import annotations

import asyncio
import os
import socket

import pytest

PRACTICE_URL = os.getenv("TEST_URL", "https://practicesoftwaretesting.com")
TEST_USERNAME = os.getenv("TEST_USERNAME", "customer@practicesoftwaretesting.com")
TEST_PASSWORD = os.getenv("TEST_PASSWORD", "welcome01")

_no_api_key = not os.getenv("GOOGLE_API_KEY")
_skip_network = os.getenv("BLOP_SKIP_NETWORK_TESTS", "0") == "1"


def _host_resolves(url: str) -> bool:
    from urllib.parse import urlparse

    hostname = urlparse(url).hostname
    if not hostname:
        return False
    try:
        socket.gethostbyname(hostname)
        return True
    except OSError:
        return False


def _chromium_launchable() -> bool:
    async def _probe() -> bool:
        try:
            from playwright.async_api import async_playwright

            async with async_playwright() as p:
                browser = await p.chromium.launch(headless=True)
                await browser.close()
            return True
        except Exception:
            return False

    try:
        return asyncio.run(_probe())
    except Exception:
        return False


_host_unreachable = not _skip_network and not _host_resolves(PRACTICE_URL)
_chromium_unavailable = not _skip_network and not _host_unreachable and not _chromium_launchable()

_skip_reason = (
    "GOOGLE_API_KEY not set — skipping live integration tests"
    if _no_api_key
    else "BLOP_SKIP_NETWORK_TESTS=1"
    if _skip_network
    else f"Host for {PRACTICE_URL} is not reachable from this environment"
    if _host_unreachable
    else "Chromium cannot launch in this environment"
    if _chromium_unavailable
    else None
)
_skip = bool(_skip_reason)


@pytest.mark.skipif(_skip, reason=_skip_reason or "")
@pytest.mark.happy_path
@pytest.mark.slow
@pytest.mark.integration
class TestHappyPath:
    """Sequential happy-path tests; each step builds on the previous."""

    _release_id: str = ""
    _run_id: str = ""

    # ------------------------------------------------------------------
    # Step 1: validate_release_setup
    # ------------------------------------------------------------------

    @pytest.mark.asyncio
    async def test_01_validate_release_setup_returns_ready(self, tmp_db):
        from blop.tools.validate import validate_release_setup

        result = await validate_release_setup(app_url=PRACTICE_URL)
        assert result["status"] in ("ready", "warnings"), (
            f"validate_release_setup returned unexpected status: {result}"
        )
        assert result["blockers"] == [], f"Unexpected blockers: {result['blockers']}"

    # ------------------------------------------------------------------
    # Step 2: save_auth_profile with env_login
    # ------------------------------------------------------------------

    @pytest.mark.asyncio
    async def test_02_save_auth_profile_env_login(self, tmp_db):
        from blop.tools.auth import save_auth_profile
        from unittest.mock import patch

        with patch.dict(os.environ, {"TEST_USERNAME": TEST_USERNAME, "TEST_PASSWORD": TEST_PASSWORD}):
            result = await save_auth_profile(
                profile_name="practice_user",
                auth_type="env_login",
                login_url=f"{PRACTICE_URL}/auth/login",
                username_env="TEST_USERNAME",
                password_env="TEST_PASSWORD",
            )

        assert result.get("status") == "saved", f"Unexpected result: {result}"

    # ------------------------------------------------------------------
    # Step 3: discover_critical_journeys — expect ≥3 flows, ≥1 gated
    # ------------------------------------------------------------------

    @pytest.mark.asyncio
    async def test_03_discover_critical_journeys_finds_revenue_journey(self, tmp_db):
        from blop.tools.journeys import discover_critical_journeys

        result = await discover_critical_journeys(
            app_url=PRACTICE_URL,
            business_goal="Find the most revenue-critical flows: product browsing, cart, checkout.",
            max_depth=2,
            max_pages=8,
        )

        assert "journeys" in result, f"Missing journeys key: {result}"
        assert result["journey_count"] >= 3, (
            f"Expected at least 3 journeys, got {result['journey_count']}"
        )
        gated = [j for j in result["journeys"] if j.get("include_in_release_gating")]
        assert len(gated) >= 1, f"Expected at least 1 gated journey, got: {result['journeys']}"

    # ------------------------------------------------------------------
    # Step 4: run_release_check in targeted mode (synchronous, no recording)
    # ------------------------------------------------------------------

    @pytest.mark.asyncio
    async def test_04_run_release_check_targeted_mode(self, tmp_db):
        from blop.tools.release_check import run_release_check
        from unittest.mock import patch

        with patch.dict(os.environ, {"TEST_USERNAME": TEST_USERNAME, "TEST_PASSWORD": TEST_PASSWORD}):
            result = await run_release_check(
                app_url=PRACTICE_URL,
                mode="targeted",
                profile_name="practice_user",
                headless=True,
            )

        assert "decision" in result, f"Missing decision: {result}"
        assert result["decision"] in ("SHIP", "INVESTIGATE", "BLOCK"), (
            f"Unexpected decision: {result['decision']}"
        )
        # Stash for later steps
        TestHappyPath._release_id = result.get("release_id", "")
        TestHappyPath._run_id = result.get("run_id", "")

    # ------------------------------------------------------------------
    # Step 5: get_test_results — status completed
    # ------------------------------------------------------------------

    @pytest.mark.asyncio
    async def test_05_get_test_results_completes(self, tmp_db):
        if not TestHappyPath._run_id:
            pytest.skip("No run_id from step 4")

        from blop.tools.results import get_test_results

        result = await get_test_results(run_id=TestHappyPath._run_id)
        # targeted mode completes synchronously
        assert result.get("status") in ("completed", "failed"), (
            f"Expected completed/failed, got: {result.get('status')}"
        )

    # ------------------------------------------------------------------
    # Step 6: triage_release_blocker — evidence fields present
    # ------------------------------------------------------------------

    @pytest.mark.asyncio
    async def test_06_triage_release_blocker_returns_evidence(self, tmp_db):
        if not TestHappyPath._run_id:
            pytest.skip("No run_id from step 4")

        from blop.tools.triage import triage_release_blocker

        result = await triage_release_blocker(run_id=TestHappyPath._run_id)
        assert "likely_cause" in result, f"Missing likely_cause: {result}"
        assert "recommended_action" in result, f"Missing recommended_action: {result}"
