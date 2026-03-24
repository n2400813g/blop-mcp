"""Tests for tools/validate.py — validate_setup tool."""
from __future__ import annotations

import os
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


@pytest.mark.asyncio
async def test_validate_all_pass_returns_ready():
    """All checks pass → status='ready'."""
    from blop.tools.validate import validate_setup

    mock_browser = AsyncMock()
    mock_playwright = AsyncMock()
    mock_playwright.__aenter__ = AsyncMock(return_value=mock_playwright)
    mock_playwright.__aexit__ = AsyncMock(return_value=False)
    mock_playwright.chromium.launch.return_value = mock_browser

    mock_resp = MagicMock()
    mock_resp.status = 200

    mock_urlopen = MagicMock()
    mock_urlopen.return_value.__enter__ = MagicMock(return_value=mock_resp)
    mock_urlopen.return_value.__exit__ = MagicMock(return_value=False)

    with patch.dict(os.environ, {"GOOGLE_API_KEY": "test_key"}):
        with patch("playwright.async_api.async_playwright", return_value=mock_playwright):
            with patch("blop.storage.sqlite.init_db", new_callable=AsyncMock):
                result = await validate_setup()

    assert result["status"] == "ready"
    assert result["blockers"] == []
    assert result["warnings"] == []
    assert "headline" in result
    assert "recommended_action" in result
    assert "runtime_posture" in result
    assert result["stability_readiness"]["ready_for_release_gating"] is True
    assert result["check_summary"]["total_checks"] >= 3
    assert any(c["name"] == "GOOGLE_API_KEY" and c["passed"] for c in result["checks"])
    assert any(c["name"] == "chromium_installed" and c["passed"] for c in result["checks"])
    assert any(c["name"] == "sqlite_db" and c["passed"] for c in result["checks"])


@pytest.mark.asyncio
async def test_validate_missing_api_key_blocked():
    """Missing GOOGLE_API_KEY → status='blocked'."""
    from blop.tools.validate import validate_setup

    mock_browser = AsyncMock()
    mock_playwright = AsyncMock()
    mock_playwright.__aenter__ = AsyncMock(return_value=mock_playwright)
    mock_playwright.__aexit__ = AsyncMock(return_value=False)
    mock_playwright.chromium.launch.return_value = mock_browser

    with patch.dict(os.environ, {}, clear=True):
        with patch("playwright.async_api.async_playwright", return_value=mock_playwright):
            with patch("blop.storage.sqlite.init_db", new_callable=AsyncMock):
                result = await validate_setup()

    assert result["status"] == "blocked"
    assert any("GOOGLE_API_KEY" in b for b in result["blockers"])
    api_check = next(c for c in result["checks"] if c["name"] == "GOOGLE_API_KEY")
    assert not api_check["passed"]
    assert result["bucketed_blockers"][0]["stability_bucket"] == "environment_runtime_misconfig"


@pytest.mark.asyncio
async def test_validate_chromium_not_installed_blocked():
    """Playwright launch raises → chromium_installed check fails → status='blocked'."""
    from blop.tools.validate import validate_setup

    mock_playwright = AsyncMock()
    mock_playwright.__aenter__ = AsyncMock(return_value=mock_playwright)
    mock_playwright.__aexit__ = AsyncMock(return_value=False)
    mock_playwright.chromium.launch.side_effect = Exception("Chromium executable not found")

    with patch.dict(os.environ, {"GOOGLE_API_KEY": "test_key"}):
        with patch("playwright.async_api.async_playwright", return_value=mock_playwright):
            with patch("blop.storage.sqlite.init_db", new_callable=AsyncMock):
                result = await validate_setup()

    assert result["status"] == "blocked"
    assert any("Chromium" in b for b in result["blockers"])
    chrom_check = next(c for c in result["checks"] if c["name"] == "chromium_installed")
    assert not chrom_check["passed"]
    assert any(issue["stability_bucket"] == "install_or_upgrade_failure" for issue in result["bucketed_blockers"])


@pytest.mark.asyncio
async def test_validate_db_init_fails_blocked():
    """init_db raises → sqlite_db check fails → status='blocked'."""
    from blop.tools.validate import validate_setup

    mock_browser = AsyncMock()
    mock_playwright = AsyncMock()
    mock_playwright.__aenter__ = AsyncMock(return_value=mock_playwright)
    mock_playwright.__aexit__ = AsyncMock(return_value=False)
    mock_playwright.chromium.launch.return_value = mock_browser

    with patch.dict(os.environ, {"GOOGLE_API_KEY": "test_key"}):
        with patch("playwright.async_api.async_playwright", return_value=mock_playwright):
            with patch("blop.storage.sqlite.init_db", side_effect=Exception("disk I/O error")):
                result = await validate_setup()

    assert result["status"] == "blocked"
    assert any("SQLite" in b or "disk" in b for b in result["blockers"])
    db_check = next(c for c in result["checks"] if c["name"] == "sqlite_db")
    assert not db_check["passed"]


@pytest.mark.asyncio
async def test_validate_app_url_unreachable_warnings():
    """app_url provided but urlopen raises → status='warnings', not 'blocked'."""
    from blop.tools.validate import validate_setup

    mock_browser = AsyncMock()
    mock_playwright = AsyncMock()
    mock_playwright.__aenter__ = AsyncMock(return_value=mock_playwright)
    mock_playwright.__aexit__ = AsyncMock(return_value=False)
    mock_playwright.chromium.launch.return_value = mock_browser

    with patch.dict(os.environ, {"GOOGLE_API_KEY": "test_key"}):
        with patch("playwright.async_api.async_playwright", return_value=mock_playwright):
            with patch("blop.storage.sqlite.init_db", new_callable=AsyncMock):
                with patch("urllib.request.urlopen", side_effect=Exception("Connection refused")):
                    result = await validate_setup(app_url="https://unreachable.example.com")

    assert result["status"] == "warnings"
    assert result["blockers"] == []
    assert any("unreachable" in w or "not reachable" in w for w in result["warnings"])
    assert result["bucketed_warnings"][0]["stability_bucket"] == "network_transient_infra"


@pytest.mark.asyncio
async def test_validate_app_url_reachable_adds_check():
    """app_url provided and reachable → app_url_reachable check added and passed."""
    from blop.tools.validate import validate_setup

    mock_browser = AsyncMock()
    mock_playwright = AsyncMock()
    mock_playwright.__aenter__ = AsyncMock(return_value=mock_playwright)
    mock_playwright.__aexit__ = AsyncMock(return_value=False)
    mock_playwright.chromium.launch.return_value = mock_browser

    mock_resp = MagicMock()
    mock_resp.status = 200

    mock_urlopen_ctx = MagicMock()
    mock_urlopen_ctx.__enter__ = MagicMock(return_value=mock_resp)
    mock_urlopen_ctx.__exit__ = MagicMock(return_value=False)

    with patch.dict(os.environ, {"GOOGLE_API_KEY": "test_key"}):
        with patch("playwright.async_api.async_playwright", return_value=mock_playwright):
            with patch("blop.storage.sqlite.init_db", new_callable=AsyncMock):
                with patch("urllib.request.urlopen", return_value=mock_urlopen_ctx):
                    result = await validate_setup(app_url="https://example.com")

    url_check = next((c for c in result["checks"] if c["name"] == "app_url_reachable"), None)
    assert url_check is not None
    assert url_check["passed"]


@pytest.mark.asyncio
async def test_validate_app_url_invalid_scheme_skips_fetch():
    from blop.tools.validate import validate_setup

    mock_browser = AsyncMock()
    mock_playwright = AsyncMock()
    mock_playwright.__aenter__ = AsyncMock(return_value=mock_playwright)
    mock_playwright.__aexit__ = AsyncMock(return_value=False)
    mock_playwright.chromium.launch.return_value = mock_browser

    with patch.dict(os.environ, {"GOOGLE_API_KEY": "test_key"}):
        with patch("playwright.async_api.async_playwright", return_value=mock_playwright):
            with patch("blop.storage.sqlite.init_db", new_callable=AsyncMock):
                with patch("urllib.request.urlopen") as urlopen_mock:
                    result = await validate_setup(app_url="file:///etc/passwd")

    assert result["status"] == "warnings"
    url_check = next((c for c in result["checks"] if c["name"] == "app_url_reachable"), None)
    assert url_check is not None
    assert url_check["passed"] is False
    assert "http or https" in url_check["message"]
    urlopen_mock.assert_not_called()


@pytest.mark.asyncio
async def test_validate_profile_not_found_warning():
    """profile_name provided but not in DB → warning, not blocker."""
    from blop.tools.validate import validate_setup

    mock_browser = AsyncMock()
    mock_playwright = AsyncMock()
    mock_playwright.__aenter__ = AsyncMock(return_value=mock_playwright)
    mock_playwright.__aexit__ = AsyncMock(return_value=False)
    mock_playwright.chromium.launch.return_value = mock_browser

    with patch.dict(os.environ, {"GOOGLE_API_KEY": "test_key"}):
        with patch("playwright.async_api.async_playwright", return_value=mock_playwright):
            with patch("blop.storage.sqlite.init_db", new_callable=AsyncMock):
                with patch("blop.storage.sqlite.get_auth_profile", new_callable=AsyncMock, return_value=None):
                    result = await validate_setup(profile_name="missing_profile")

    assert result["status"] == "warnings"
    assert any("missing_profile" in w for w in result["warnings"])
    auth_check = next((c for c in result["checks"] if c["name"] == "auth_profile"), None)
    assert auth_check is not None
    assert not auth_check["passed"]
    assert result["bucketed_warnings"][0]["stability_bucket"] == "auth_session_failure"
    assert result["stability_readiness"]["primary_bucket"] == "auth_session_failure"


@pytest.mark.asyncio
async def test_validate_setup_records_bucketed_validation_observations():
    from blop.tools.validate import validate_setup

    mock_browser = AsyncMock()
    mock_playwright = AsyncMock()
    mock_playwright.__aenter__ = AsyncMock(return_value=mock_playwright)
    mock_playwright.__aexit__ = AsyncMock(return_value=False)
    mock_playwright.chromium.launch.return_value = mock_browser

    with patch.dict(os.environ, {"GOOGLE_API_KEY": "test_key"}):
        with patch("playwright.async_api.async_playwright", return_value=mock_playwright):
            with patch("blop.storage.sqlite.init_db", new_callable=AsyncMock):
                with patch("urllib.request.urlopen", side_effect=Exception("Connection refused")):
                    with patch("blop.storage.sqlite.save_telemetry_signals", new_callable=AsyncMock) as save_signals:
                        result = await validate_setup(app_url="https://unreachable.example.com")

    assert result["bucketed_warnings"][0]["stability_bucket"] == "network_transient_infra"
    save_signals.assert_awaited()


@pytest.mark.asyncio
async def test_validate_release_setup_suggested_steps_use_canonical_names():
    """validate_release_setup rewrites discover_test_flows → discover_critical_journeys."""
    from blop.tools.validate import validate_release_setup

    mock_browser = AsyncMock()
    mock_playwright = AsyncMock()
    mock_playwright.__aenter__ = AsyncMock(return_value=mock_playwright)
    mock_playwright.__aexit__ = AsyncMock(return_value=False)
    mock_playwright.chromium.launch.return_value = mock_browser
    mock_resp = MagicMock()
    mock_resp.status = 200
    mock_urlopen_ctx = MagicMock()
    mock_urlopen_ctx.__enter__ = MagicMock(return_value=mock_resp)
    mock_urlopen_ctx.__exit__ = MagicMock(return_value=False)

    with patch.dict(os.environ, {"GOOGLE_API_KEY": "test_key"}):
        with patch("playwright.async_api.async_playwright", return_value=mock_playwright):
            with patch("blop.storage.sqlite.init_db", new_callable=AsyncMock):
                with patch("urllib.request.urlopen", return_value=mock_urlopen_ctx):
                    result = await validate_release_setup(app_url="https://example.com")

    steps_text = " ".join(result.get("suggested_next_steps", []))
    assert "discover_critical_journeys" in steps_text
    assert "discover_test_flows" not in steps_text
    assert "run_release_check" in steps_text
    assert "flow_ids" in steps_text
    assert "journey_ids" not in steps_text


@pytest.mark.asyncio
async def test_validate_release_setup_delegates_to_validate_setup():
    """validate_release_setup returns the same status as validate_setup."""
    from blop.tools.validate import validate_release_setup, validate_setup

    mock_browser = AsyncMock()
    mock_playwright = AsyncMock()
    mock_playwright.__aenter__ = AsyncMock(return_value=mock_playwright)
    mock_playwright.__aexit__ = AsyncMock(return_value=False)
    mock_playwright.chromium.launch.return_value = mock_browser

    with patch.dict(os.environ, {"GOOGLE_API_KEY": "test_key"}):
        with patch("playwright.async_api.async_playwright", return_value=mock_playwright):
            with patch("blop.storage.sqlite.init_db", new_callable=AsyncMock):
                result_setup = await validate_setup()
                result_release = await validate_release_setup()

    assert result_setup["status"] == result_release["status"]
    assert result_release["headline"] == result_setup["headline"]


@pytest.mark.asyncio
async def test_validate_release_setup_auth_warning_prioritizes_refresh():
    from blop.tools.validate import validate_release_setup

    mock_browser = AsyncMock()
    mock_playwright = AsyncMock()
    mock_playwright.__aenter__ = AsyncMock(return_value=mock_playwright)
    mock_playwright.__aexit__ = AsyncMock(return_value=False)
    mock_playwright.chromium.launch.return_value = mock_browser
    mock_resp = MagicMock()
    mock_resp.status = 200
    mock_urlopen_ctx = MagicMock()
    mock_urlopen_ctx.__enter__ = MagicMock(return_value=mock_resp)
    mock_urlopen_ctx.__exit__ = MagicMock(return_value=False)

    profile = MagicMock()
    profile.auth_type = "storage_state"

    with patch.dict(os.environ, {"GOOGLE_API_KEY": "test_key"}):
        with patch("playwright.async_api.async_playwright", return_value=mock_playwright):
            with patch("blop.storage.sqlite.init_db", new_callable=AsyncMock):
                with patch("urllib.request.urlopen", return_value=mock_urlopen_ctx):
                    with patch("blop.storage.sqlite.get_auth_profile", new_callable=AsyncMock, return_value=profile):
                        with patch(
                            "blop.engine.auth.resolve_storage_state_for_profile",
                            new_callable=AsyncMock,
                            return_value="/tmp/auth.json",
                        ):
                            with patch("blop.engine.auth.validate_auth_session", new_callable=AsyncMock, return_value=False):
                                result = await validate_release_setup(
                                    app_url="https://example.com",
                                    profile_name="prod",
                                )

    assert result["status"] == "warnings"
    assert "expired" in result["headline"].lower()
    assert "capture_auth_session" in result["recommended_action"]
    assert "capture_auth_session" in result["suggested_next_steps"][0]
    assert "validate_release_setup" in result["suggested_next_steps"][1]


@pytest.mark.asyncio
async def test_validate_profile_valid_passes():
    """profile_name provided and resolves successfully → auth_profile check passes."""
    from blop.tools.validate import validate_setup
    from blop.schemas import AuthProfile

    mock_browser = AsyncMock()
    mock_playwright = AsyncMock()
    mock_playwright.__aenter__ = AsyncMock(return_value=mock_playwright)
    mock_playwright.__aexit__ = AsyncMock(return_value=False)
    mock_playwright.chromium.launch.return_value = mock_browser

    mock_profile = AuthProfile(
        profile_name="prod",
        auth_type="env_login",
        login_url="https://example.com/login",
    )

    with patch.dict(os.environ, {"GOOGLE_API_KEY": "test_key"}):
        with patch("playwright.async_api.async_playwright", return_value=mock_playwright):
            with patch("blop.storage.sqlite.init_db", new_callable=AsyncMock):
                with patch("blop.storage.sqlite.get_auth_profile", new_callable=AsyncMock, return_value=mock_profile):
                    with patch("blop.engine.auth.resolve_storage_state", new_callable=AsyncMock, return_value='{"cookies":[]}'):
                        result = await validate_setup(profile_name="prod")

    assert result["status"] == "ready"
    auth_check = next((c for c in result["checks"] if c["name"] == "auth_profile"), None)
    assert auth_check is not None
    assert auth_check["passed"]


@pytest.mark.asyncio
async def test_validate_expired_storage_state_surfaces_primary_action():
    from blop.tools.validate import validate_setup
    from blop.schemas import AuthProfile

    mock_browser = AsyncMock()
    mock_playwright = AsyncMock()
    mock_playwright.__aenter__ = AsyncMock(return_value=mock_playwright)
    mock_playwright.__aexit__ = AsyncMock(return_value=False)
    mock_playwright.chromium.launch.return_value = mock_browser

    mock_profile = AuthProfile(
        profile_name="prod",
        auth_type="storage_state",
        storage_state_path="/tmp/prod.json",
    )

    with patch.dict(os.environ, {"GOOGLE_API_KEY": "test_key"}):
        with patch("playwright.async_api.async_playwright", return_value=mock_playwright):
            with patch("blop.storage.sqlite.init_db", new_callable=AsyncMock):
                with patch("blop.storage.sqlite.get_auth_profile", new_callable=AsyncMock, return_value=mock_profile):
                    with patch("blop.engine.auth.resolve_storage_state_for_profile", new_callable=AsyncMock, return_value="/tmp/prod.json"):
                        with patch("blop.engine.auth.validate_auth_session", new_callable=AsyncMock, return_value=False):
                            result = await validate_setup(app_url="https://example.com", profile_name="prod")

    assert result["status"] == "warnings"
    assert "expired" in " ".join(result["warnings"]).lower()
    assert "capture_auth_session" in result["recommended_action"]
    assert "expired" in result["headline"].lower()
