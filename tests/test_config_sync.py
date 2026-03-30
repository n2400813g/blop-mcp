import importlib

import blop.config as config_module


def _reload_config(monkeypatch, env_overrides: dict):
    """Reload blop.config after applying env overrides, return the reloaded module."""
    for key, val in env_overrides.items():
        if val is None:
            monkeypatch.delenv(key, raising=False)
        else:
            monkeypatch.setenv(key, val)
    importlib.reload(config_module)
    return config_module


def test_is_cloud_sync_configured_all_set(monkeypatch):
    mod = _reload_config(
        monkeypatch,
        {
            "BLOP_HOSTED_URL": "https://app.blop.ai",
            "BLOP_API_TOKEN": "blop_sk_test",
            "BLOP_PROJECT_ID": "proj-123",
        },
    )
    assert mod.is_cloud_sync_configured() is True


def test_is_cloud_sync_configured_partial(monkeypatch):
    mod = _reload_config(
        monkeypatch,
        {
            "BLOP_HOSTED_URL": "https://app.blop.ai",
            "BLOP_API_TOKEN": None,
            "BLOP_PROJECT_ID": None,
        },
    )
    assert mod.is_cloud_sync_configured() is False
    missing = mod.cloud_sync_missing_vars()
    assert "BLOP_API_TOKEN" in missing
    assert "BLOP_PROJECT_ID" in missing


def test_default_contract_version(monkeypatch):
    mod = _reload_config(monkeypatch, {"BLOP_RUNTIME_CONTRACT_VERSION": None})
    assert mod.BLOP_RUNTIME_CONTRACT_VERSION == "2026-03-29"
