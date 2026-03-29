"""Journey-scoped API expectation verification against observed network traffic."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from blop.schemas import RecordedFlow


def _method_matches(observed_method: str, expected_methods: list[str]) -> bool:
    if not expected_methods:
        return True
    return observed_method.upper() in {method.upper() for method in expected_methods}


def evaluate_api_expectations(flow: "RecordedFlow", network_observations: list[dict]) -> list[dict]:
    """Return normalized per-expectation verification results."""
    results: list[dict] = []
    for expectation in getattr(flow, "api_expectations", []) or []:
        if isinstance(expectation, dict):
            url_contains = str(expectation.get("url_contains") or "")
            methods = list(expectation.get("methods") or [])
            min_status = int(expectation.get("min_status") or 200)
            max_status = int(expectation.get("max_status") or 399)
            required = bool(expectation.get("required", True))
            name = str(expectation.get("name") or url_contains or "api_expectation")
            description = str(expectation.get("description") or name)
        else:
            url_contains = str(getattr(expectation, "url_contains", "") or "")
            methods = list(getattr(expectation, "methods", []) or [])
            min_status = int(getattr(expectation, "min_status", 200) or 200)
            max_status = int(getattr(expectation, "max_status", 399) or 399)
            required = bool(getattr(expectation, "required", True))
            name = str(getattr(expectation, "name", None) or url_contains or "api_expectation")
            description = str(getattr(expectation, "description", None) or name)

        matching = [
            obs
            for obs in network_observations
            if url_contains in str(obs.get("url", "")) and _method_matches(str(obs.get("method", "")), methods)
        ]
        observed_statuses = [int(obs.get("status", 0) or 0) for obs in matching if obs.get("status") is not None]
        passed = bool(matching) and all(min_status <= status <= max_status for status in observed_statuses)

        results.append(
            {
                "expectation": name,
                "description": description,
                "required": required,
                "passed": passed,
                "url_contains": url_contains,
                "methods": methods,
                "observed_count": len(matching),
                "observed_statuses": observed_statuses[:10],
                "sample_urls": [str(obs.get("url", "")) for obs in matching[:3]],
            }
        )
    return results
