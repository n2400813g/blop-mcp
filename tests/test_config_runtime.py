from __future__ import annotations

from blop import config


def test_validate_app_url_blocks_localhost_by_default(monkeypatch):
    monkeypatch.setattr(config, "BLOP_ALLOW_INTERNAL_URLS", False)
    monkeypatch.setattr(config, "BLOP_ALLOWED_HOSTS", ())
    err = config.validate_app_url("http://localhost:3000")
    assert err is not None
    assert "internal host" in err


def test_validate_app_url_allows_localhost_when_opted_in(monkeypatch):
    monkeypatch.setattr(config, "BLOP_ALLOW_INTERNAL_URLS", True)
    monkeypatch.setattr(config, "BLOP_ALLOWED_HOSTS", ())
    err = config.validate_app_url("http://localhost:3000")
    assert err is None


def test_validate_mobile_replay_app_url_accepts_package_name():
    assert config.validate_mobile_replay_app_url("com.example.app") is None


def test_validate_mobile_replay_app_url_rejects_empty():
    err = config.validate_mobile_replay_app_url("")
    assert err is not None


def test_runtime_config_issues_require_absolute_paths(monkeypatch):
    monkeypatch.setattr(config, "BLOP_REQUIRE_ABSOLUTE_PATHS", True)
    monkeypatch.setattr(config, "BLOP_ENV", "production")
    monkeypatch.setattr(config, "BLOP_ALLOW_INTERNAL_URLS", True)
    monkeypatch.setenv("BLOP_DB_PATH", ".blop/runs.db")
    monkeypatch.setenv("BLOP_RUNS_DIR", "runs")
    monkeypatch.setenv("BLOP_DEBUG_LOG", ".blop/blop.log")

    errors, warnings = config.runtime_config_issues()
    assert any("BLOP_DB_PATH must be an absolute path" in e for e in errors)
    assert any("BLOP_RUNS_DIR must be an absolute path" in e for e in errors)
    assert any("BLOP_DEBUG_LOG must be an absolute path" in e for e in errors)
    assert any("BLOP_ALLOW_INTERNAL_URLS=true in production" in w for w in warnings)


def test_runtime_posture_snapshot_surfaces_key_runtime_state(monkeypatch):
    monkeypatch.setattr(config, "BLOP_ENV", "production")
    monkeypatch.setattr(config, "BLOP_LLM_PROVIDER", "google")
    monkeypatch.setattr(config, "BLOP_DB_PATH", "/var/lib/blop/runs.db")
    monkeypatch.setattr(config, "BLOP_RUNS_DIR", "/var/lib/blop/runs")
    monkeypatch.setattr(config, "BLOP_DEBUG_LOG", "/var/log/blop/blop.log")
    monkeypatch.setattr(config, "BLOP_REQUIRE_ABSOLUTE_PATHS", True)
    monkeypatch.setattr(config, "BLOP_ALLOW_INTERNAL_URLS", False)
    monkeypatch.setattr(config, "BLOP_ALLOWED_HOSTS", ("app.example.com",))
    monkeypatch.setattr(config, "BLOP_ENABLE_COMPAT_TOOLS", False)
    monkeypatch.setattr(config, "BLOP_ENABLE_LEGACY_MCP_TOOLS", False)
    monkeypatch.setattr(config, "BLOP_HOSTED_URL", "https://cloud.blop.dev")
    monkeypatch.setattr(config, "BLOP_API_TOKEN", "blop_sk_test")
    monkeypatch.setattr(config, "BLOP_PROJECT_ID", "proj_123")
    monkeypatch.setattr(config, "BLOP_RUN_TIMEOUT_SECS", 1800)
    monkeypatch.setattr(config, "BLOP_STEP_TIMEOUT_SECS", 45)
    monkeypatch.setenv("BLOP_CAPABILITIES_PROFILE", "production_minimal")
    monkeypatch.setattr(config, "check_llm_api_key", lambda: (True, "GOOGLE_API_KEY"))

    posture = config.runtime_posture_snapshot()

    assert posture["environment"] == "production"
    assert posture["llm_key_present"] is True
    assert posture["capabilities_profile"] == "production_minimal"
    assert posture["legacy_mcp_tools_enabled"] is False
    assert posture["concurrency"]["discovery_workers"] == config.BLOP_DISCOVERY_CONCURRENCY
    assert posture["concurrency"]["replay_workers"] == config.BLOP_REPLAY_CONCURRENCY
    assert posture["paths"]["db_path_absolute"] is True
    assert posture["paths"]["runs_dir_absolute"] is True
    assert posture["paths"]["debug_log_absolute"] is True
    assert posture["hosted_sync"]["enabled"] is True
    assert posture["hosted_sync"]["project_id"] == "proj_123"


def test_hosted_sync_snapshot_marks_partial_configuration(monkeypatch):
    monkeypatch.setattr(config, "BLOP_HOSTED_URL", "https://cloud.blop.dev")
    monkeypatch.setattr(config, "BLOP_API_TOKEN", None)
    monkeypatch.setattr(config, "BLOP_PROJECT_ID", "proj_123")

    snapshot = config.hosted_sync_config_snapshot()

    assert snapshot["enabled"] is False
    assert snapshot["partial"] is True
    assert "BLOP_API_TOKEN" in snapshot["missing_fields"]
