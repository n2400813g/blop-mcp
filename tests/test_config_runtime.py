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
