from __future__ import annotations

import importlib


def test_capabilities_profile_overrides_explicit_caps(monkeypatch):
    monkeypatch.setenv("BLOP_CAPABILITIES_PROFILE", "production_minimal")
    monkeypatch.setenv("BLOP_CAPABILITIES", "core,auth,debug,compat_browser")

    import blop.capabilities as capabilities

    capabilities = importlib.reload(capabilities)
    enabled = capabilities.get_enabled_capabilities()
    assert enabled == ["core", "auth"]


def test_capabilities_profile_fallback_to_explicit_caps_when_unknown(monkeypatch):
    monkeypatch.setenv("BLOP_CAPABILITIES_PROFILE", "unknown_profile")
    monkeypatch.setenv("BLOP_CAPABILITIES", "core,auth,debug")

    import blop.capabilities as capabilities

    capabilities = importlib.reload(capabilities)
    enabled = capabilities.get_enabled_capabilities()
    assert enabled == ["core", "auth", "debug"]
