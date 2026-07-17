from __future__ import annotations


def test_capabilities_profile_overrides_explicit_caps(monkeypatch):
    import blop.config as cfg

    monkeypatch.setattr(cfg, "BLOP_CAPABILITIES_PROFILE", "production_minimal")
    monkeypatch.setattr(cfg, "BLOP_CAPABILITIES", "core,auth,debug,compat_browser")

    from blop.capabilities import get_enabled_capabilities

    enabled = get_enabled_capabilities()
    assert enabled == ["core", "auth"]


def test_capabilities_profile_fallback_to_explicit_caps_when_unknown(monkeypatch):
    import blop.config as cfg

    monkeypatch.setattr(cfg, "BLOP_CAPABILITIES_PROFILE", "unknown_profile")
    monkeypatch.setattr(cfg, "BLOP_CAPABILITIES", "core,auth,debug")

    from blop.capabilities import get_enabled_capabilities

    enabled = get_enabled_capabilities()
    assert enabled == ["core", "auth", "debug"]
