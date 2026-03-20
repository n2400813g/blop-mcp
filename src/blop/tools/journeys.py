"""discover_critical_journeys — MVP canonical tool for journey discovery."""
from __future__ import annotations

import re
import uuid
from typing import Optional

from blop.config import BLOP_DISCOVERY_MAX_PAGES, validate_app_url
from blop.engine import discovery
from blop.storage import sqlite


async def discover_critical_journeys(
    app_url: str,
    profile_name: Optional[str] = None,
    business_goal: Optional[str] = None,
    max_depth: int = 2,
    max_pages: int = BLOP_DISCOVERY_MAX_PAGES,
    seed_urls: Optional[list[str]] = None,
    include_url_pattern: Optional[str] = None,
    exclude_url_pattern: Optional[str] = None,
) -> dict:
    """Crawl app_url and plan 3-8 critical user journeys in business language.

    Returns CriticalJourney objects that map directly to ship/no-ship decisions.
    Each journey includes a why_it_matters field and include_in_release_gating flag
    so you can immediately scope which journeys gate a release.
    """
    url_err = validate_app_url(app_url)
    if url_err:
        return {"error": url_err}
    for param_name, pattern in [("include_url_pattern", include_url_pattern), ("exclude_url_pattern", exclude_url_pattern)]:
        if pattern:
            try:
                re.compile(pattern)
            except re.error as exc:
                return {"error": f"Invalid {param_name}: {exc}"}

    result = await discovery.discover_flows(
        app_url=app_url,
        profile_name=profile_name,
        business_goal=business_goal,
        max_depth=max_depth,
        max_pages=max_pages,
        seed_urls=seed_urls,
        include_url_pattern=include_url_pattern,
        exclude_url_pattern=exclude_url_pattern,
    )

    if "error" in result:
        return result

    # Build a name → flow_id index from recorded flows so we can link journeys
    recorded = await sqlite.list_flows()
    name_to_flow_id: dict[str, str] = {r["flow_name"]: r["flow_id"] for r in recorded}

    raw_flows: list[dict] = result.get("flows", [])
    journeys = []
    for flow_dict in raw_flows:
        journey = _flow_dict_to_critical_journey(flow_dict, name_to_flow_id)
        journeys.append(journey)

    gated = [j for j in journeys if j["include_in_release_gating"]]
    return {
        "journeys": journeys,
        "journey_count": len(journeys),
        "release_gating_count": len(gated),
        "resource_link": "blop://journeys",
        "workflow_hint": (
            f"Found {len(journeys)} critical journeys ({len(gated)} gated for release). "
            "Next: run_release_check(app_url=..., journey_ids=[...]) to evaluate release confidence."
        ),
    }


def _flow_dict_to_critical_journey(
    flow_dict: dict,
    name_to_flow_id: dict[str, str],
) -> dict:
    """Convert a raw flow dict from discover_flows into a CriticalJourney shape."""
    journey_name = flow_dict.get("flow_name") or flow_dict.get("name", "Unknown Journey")
    goal = flow_dict.get("goal", "")
    criticality = flow_dict.get("business_criticality", "other")
    if criticality not in ("revenue", "activation", "retention", "support", "other"):
        criticality = "other"
    confidence = float(flow_dict.get("confidence", 0.7))
    auth_required = bool(flow_dict.get("auth_required", False))
    likely_assertions = flow_dict.get("likely_assertions", [])

    # Why it matters: use goal directly (already business-language from Gemini)
    why_it_matters = goal or f"{journey_name} user journey"
    if likely_assertions:
        assertion_str = "; ".join(str(a) for a in likely_assertions[:2])
        why_it_matters = f"{why_it_matters}. Key checkpoints: {assertion_str}"

    include_in_release_gating = criticality in ("revenue", "activation")

    # Link to existing RecordedFlow if name matches
    flow_id = name_to_flow_id.get(journey_name)

    return {
        "journey_id": flow_dict.get("flow_id") or uuid.uuid4().hex,
        "journey_name": journey_name,
        "why_it_matters": why_it_matters,
        "criticality_class": criticality,
        "auth_required": auth_required,
        "confidence": confidence,
        "include_in_release_gating": include_in_release_gating,
        "flow_id": flow_id,
    }
