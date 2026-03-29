"""Lightweight defect categorisation helpers — no I/O, no LLM calls."""

from __future__ import annotations


def categorize_failure_reason(reason: str | None) -> str:
    """Map a free-text failure reason to a coarse defect category."""
    if not reason:
        return "functional"
    r = reason.lower()
    if any(k in r for k in ("api", "network", "request", "response", "status", "endpoint", "http")):
        return "integration"
    if any(
        k in r
        for k in (
            "csrf",
            "xss",
            "sqli",
            "sql injection",
            "injection",
            "auth bypass",
            "session hijack",
            "cve-",
            "vulnerability",
            "penetration",
            "oauth misconfig",
            "csp violation",
            "cors policy",
        )
    ):
        return "security"
    if any(k in r for k in ("timeout", "slow", "performance", "lcp", "ttfb", "speed")):
        return "performance"
    if any(k in r for k in ("visual", "screenshot", "ui", "layout", "display", "element", "selector")):
        return "ui"
    return "functional"
