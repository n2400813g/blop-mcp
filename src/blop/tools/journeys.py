"""discover_critical_journeys — MVP canonical tool for journey discovery."""

from __future__ import annotations

import hashlib
import re
from typing import Annotated, Literal, Optional

from pydantic import Field

from blop.config import BLOP_DISCOVERY_MAX_PAGES
from blop.engine import discovery
from blop.engine.errors import BLOP_VALIDATION_FAILED, tool_error
from blop.mcp.tool_args import require_resolved_app_url
from blop.schemas import CriticalJourney
from blop.storage import sqlite


def _planning_journey_id(app_url: str, journey_name: str, goal: str) -> str:
    """Return a deterministic planning-only journey identifier for unrecorded flows."""
    raw = "|".join(
        [
            (app_url or "").strip().lower(),
            (journey_name or "").strip().lower(),
            (goal or "").strip().lower(),
        ]
    )
    digest = hashlib.sha1(raw.encode("utf-8")).hexdigest()[:12]
    return f"planned_journey_{digest}"


def _clean_text(value: str | None, fallback: str) -> str:
    text = " ".join((value or "").split()).strip()
    return text or fallback


async def discover_critical_journeys(
    app_url: Optional[str] = None,
    profile_name: Optional[str] = None,
    business_goal: Optional[str] = None,
    business_criticality: Annotated[
        Optional[Literal["revenue", "activation", "retention", "support", "other"]],
        Field(
            default=None,
            description=(
                "Primary business category for flows. "
                "revenue: checkout/billing/subscriptions. "
                "activation: onboarding/first-run. "
                "retention: core features users return for. "
                "support: help/error recovery. "
                "other: informational or low-stakes flows."
            ),
        ),
    ] = None,
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
    resolved, err = require_resolved_app_url(app_url, field_label="app_url")
    if err:
        return err
    app_url = resolved
    for param_name, pattern in [
        ("include_url_pattern", include_url_pattern),
        ("exclude_url_pattern", exclude_url_pattern),
    ]:
        if pattern:
            try:
                re.compile(pattern)
            except re.error as exc:
                return tool_error(f"Invalid {param_name}: {exc}", BLOP_VALIDATION_FAILED, details={"param": param_name})

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
        journey = _flow_dict_to_critical_journey(flow_dict, name_to_flow_id, app_url=app_url)
        journeys.append(journey)

    gated = [j for j in journeys if j["include_in_release_gating"]]
    recommended_flow_ids = [j["flow_id"] for j in gated if j.get("flow_id")]
    return {
        "journeys": journeys,
        "journey_count": len(journeys),
        "release_gating_count": len(gated),
        "recommended_flow_ids": recommended_flow_ids,
        "crawl_diagnostics": result.get("crawl_diagnostics", {}),
        "id_contract": {
            "journey_id": "planning identifier only; do not pass to execution tools",
            "flow_id": "recorded flow identifier; pass to run_release_check(flow_ids=[...]) or triage_release_blocker(flow_id=...)",
        },
        "resource_link": "blop://journeys",
        "workflow_hint": (
            f"Found {len(journeys)} critical journeys ({len(gated)} gated for release). "
            "Next: record any missing gated journeys, then call run_release_check(app_url=..., flow_ids=[...], mode='replay')."
        ),
    }


def _flow_dict_to_critical_journey(
    flow_dict: dict,
    name_to_flow_id: dict[str, str],
    *,
    app_url: str | None = None,
) -> dict:
    """Convert a raw flow dict from discover_flows into a CriticalJourney shape."""
    journey_name = _clean_text(flow_dict.get("flow_name") or flow_dict.get("name"), "Unknown Journey")
    goal = _clean_text(flow_dict.get("goal"), f"{journey_name} user journey")
    criticality = flow_dict.get("business_criticality", "other")
    if criticality not in ("revenue", "activation", "retention", "support", "other"):
        criticality = "other"
    try:
        confidence = max(0.0, min(1.0, float(flow_dict.get("confidence", 0.7))))
    except (TypeError, ValueError):
        confidence = 0.7
    auth_required = bool(flow_dict.get("auth_required", False))
    likely_assertions = flow_dict.get("likely_assertions", [])

    # Why it matters: use goal directly (already business-language from Gemini)
    why_it_matters = goal
    if likely_assertions:
        assertion_str = "; ".join(_clean_text(str(a), "") for a in likely_assertions[:2] if _clean_text(str(a), ""))
        why_it_matters = f"{why_it_matters}. Key checkpoints: {assertion_str}"

    include_in_release_gating = criticality in ("revenue", "activation")

    # Link to existing RecordedFlow if name matches
    flow_id = name_to_flow_id.get(journey_name)
    journey_id = flow_dict.get("flow_id") or _planning_journey_id(
        flow_dict.get("starting_url") or app_url or "",
        journey_name,
        goal,
    )
    execution_status = "recorded" if flow_id else "planned_only"
    if include_in_release_gating:
        gating_reason = (
            f"{criticality.title()} journey gates release decisions."
            if criticality in ("revenue", "activation")
            else "Included in release gating."
        )
    else:
        gating_reason = (
            f"{criticality.title()} journey is useful context but does not gate release by default."
            if criticality != "other"
            else "Informational journey; not used for release gating by default."
        )

    canonical = CriticalJourney.model_validate(
        {
            "journey_id": journey_id,
            "journey_name": journey_name,
            "why_it_matters": why_it_matters,
            "criticality_class": criticality,
            "auth_required": auth_required,
            "confidence": confidence,
            "include_in_release_gating": include_in_release_gating,
            "flow_id": flow_id,
        }
    ).model_dump()

    return {
        **canonical,
        "journey_id": journey_id,
        "gating_reason": gating_reason,
        "execution_status": execution_status,
        # Aliases for prompts / older integrations that expect discovery-shaped keys
        "flow_name": journey_name,
        "goal": goal,
        "business_criticality": criticality,
    }
