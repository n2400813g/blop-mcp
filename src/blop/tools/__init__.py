"""Lazy public surface for the canonical tool package.

Avoid importing the entire compat/discovery stack when a caller only needs one
tool module such as ``blop.tools.validate``.
"""

from __future__ import annotations

from importlib import import_module

__all__ = [
    "compat",
    "discover_critical_journeys",
    "run_release_check",
    "triage_release_blocker",
    "validate_release_setup",
]

_LAZY_EXPORTS: dict[str, tuple[str, str | None]] = {
    "compat": ("blop.tools.compat", None),
    "discover_critical_journeys": ("blop.tools.journeys", "discover_critical_journeys"),
    "run_release_check": ("blop.tools.release_check", "run_release_check"),
    "triage_release_blocker": ("blop.tools.triage", "triage_release_blocker"),
    "validate_release_setup": ("blop.tools.validate", "validate_release_setup"),
}


def __getattr__(name: str):
    try:
        module_name, attr_name = _LAZY_EXPORTS[name]
    except KeyError as exc:
        raise AttributeError(f"module 'blop.tools' has no attribute {name!r}") from exc

    module = import_module(module_name)
    return module if attr_name is None else getattr(module, attr_name)


def __dir__() -> list[str]:
    return sorted(set(globals()) | set(__all__))
