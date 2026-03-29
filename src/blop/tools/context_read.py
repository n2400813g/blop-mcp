"""Context-first MCP tools: thin wrappers over resources + SQLite."""

from __future__ import annotations

import os
import time
from importlib import import_module
from typing import Optional

from blop.config import BLOP_API_TOKEN, BLOP_ENV, BLOP_HOSTED_URL, BLOP_PROJECT_ID
from blop.mcp.dto import (
    JourneyListDTO,
    PrdSummaryDTO,
    ReleaseAndJourneysDTO,
    ReleaseContextDTO,
    UxTaxonomyDTO,
    WorkspaceContextDTO,
)
from blop.mcp.envelope import err_response, ok_response
from blop.schemas import ReleaseBrief
from blop.storage import sqlite

_CACHE_TTL_SECS = 120.0
_workspace_cache: tuple[float, dict] | None = None
_ux_cache: tuple[float, dict] | None = None
_release_context_cache: dict[str, tuple[float, dict]] = {}
_journeys_for_release_cache: dict[str, tuple[float, dict]] = {}


def _resources_tools():
    return import_module("blop.tools.resources")


def _norm_url(url: str) -> str:
    return url.strip().rstrip("/")


def _release_resource_links(release_id: str) -> dict[str, str]:
    rid = release_id.strip()
    return {
        "brief": f"blop://release/{rid}/brief",
        "artifacts": f"blop://release/{rid}/artifacts",
        "incidents": f"blop://release/{rid}/incidents",
    }


def _hint_for_release_decision(decision: str | None, run_id: str | None) -> str:
    if not decision:
        if run_id:
            return (
                f"Poll get_test_results(run_id={run_id!r}) or read blop://release/{{id}}/brief "
                "until the release decision is terminal."
            )
        return "Run run_release_check(...) to create or refresh this release brief."
    if decision == "SHIP":
        return "Decision SHIP — optional: capture context graph or archive old runs per policy."
    if decision == "BLOCK":
        return "Decision BLOCK — call triage_release_blocker(release_id=...) for evidence and next actions."
    if decision == "INVESTIGATE":
        return (
            "Decision INVESTIGATE — review top_actions and failed journeys; "
            "use triage_release_blocker if blockers persist."
        )
    return "Review release brief and linked artifacts."


async def get_workspace_context(use_cache: bool = True) -> dict:
    """Compact workspace descriptor for agents."""
    global _workspace_cache
    now = time.monotonic()
    if use_cache and _workspace_cache and (now - _workspace_cache[0]) < _CACHE_TTL_SECS:
        return _workspace_cache[1]

    from blop.config import get_exploration_tuning

    tuning = get_exploration_tuning()
    workspace_id = BLOP_PROJECT_ID or "default"
    dto = WorkspaceContextDTO(
        workspace_id=workspace_id,
        environment=BLOP_ENV,
        exploration_profile=str(os.environ.get("BLOP_EXPLORATION_PROFILE", "default")),
        resource_uris={
            "journeys": "blop://journeys",
            "health": "blop://health",
            "run_mobile_artifacts": "blop://run/{run_id}/mobile_artifacts",
            "release_brief": "blop://release/{release_id}/brief",
            "release_artifacts": "blop://release/{release_id}/artifacts",
            "release_incidents": "blop://release/{release_id}/incidents",
        },
        primary_tools=[
            "validate_release_setup",
            "get_release_and_journeys",
            "get_mcp_capabilities",
            "discover_critical_journeys",
            "run_release_check",
            "get_test_results",
            "triage_release_blocker",
        ],
        recommended_next_action_hint=(
            "Preflight with validate_release_setup(app_url=…); load batched context via "
            "get_release_and_journeys(release_id) or get_workspace_context(); probe with get_mcp_capabilities()."
        ),
        hosted_sync={
            "hosted_url": BLOP_HOSTED_URL,
            "api_token_configured": bool(BLOP_API_TOKEN),
        },
        # expose tuning summary for planning (small)
    )
    payload = ok_response(
        {
            **dto.model_dump(),
            "discovery_defaults": {
                "max_pages": tuning["discover_max_pages"],
                "network_idle_wait_secs": tuning["network_idle_wait_secs"],
                "spa_settle_ms": tuning["spa_settle_ms"],
            },
        }
    ).model_dump()
    _workspace_cache = (now, payload)
    return payload


async def get_release_context(release_id: str, use_cache: bool = True) -> dict:
    global _release_context_cache
    rid = release_id.strip()
    now = time.monotonic()
    if use_cache and rid in _release_context_cache:
        ts, payload = _release_context_cache[rid]
        if (now - ts) < _CACHE_TTL_SECS:
            return payload

    raw = await _resources_tools().release_brief_resource(rid)
    if raw.get("error"):
        return err_response("not_found", raw["error"], detail=rid).model_dump()
    links = _release_resource_links(rid)
    decision = raw.get("decision")
    run_id = raw.get("run_id")
    dto = ReleaseContextDTO(
        release_id=raw.get("release_id", rid),
        run_id=run_id,
        app_url=raw.get("app_url"),
        created_at=raw.get("created_at"),
        decision=decision,
        risk=raw.get("risk"),
        confidence=raw.get("confidence"),
        blocker_count=raw.get("blocker_count"),
        blocker_journey_names=list(raw.get("blocker_journey_names") or []),
        critical_journey_failures=raw.get("critical_journey_failures"),
        top_actions=[a for a in (raw.get("top_actions") or [])],
        context_graph_summary=raw.get("context_graph_summary"),
        resource_links=links,
        recommended_next_action_hint=_hint_for_release_decision(
            str(decision) if decision is not None else None,
            str(run_id) if run_id is not None else None,
        ),
        error=None,
    )
    payload = ok_response(dto.model_dump()).model_dump()
    _release_context_cache[rid] = (now, payload)
    return payload


async def get_journeys_for_release(
    release_id: Optional[str] = None,
    app_url: Optional[str] = None,
    use_cache: bool = True,
) -> dict:
    cache_key = f"{release_id or ''}\x00{app_url or ''}"
    global _journeys_for_release_cache
    now = time.monotonic()
    if use_cache and cache_key in _journeys_for_release_cache:
        ts, payload = _journeys_for_release_cache[cache_key]
        if (now - ts) < _CACHE_TTL_SECS:
            return payload

    target_url: str | None = None
    if app_url:
        target_url = _norm_url(app_url)
    elif release_id:
        brief = await sqlite.get_release_brief(release_id)
        if not brief:
            return err_response(
                "not_found",
                f"No release brief for release_id={release_id!r}",
                detail="run_release_check first or pass app_url",
            ).model_dump()
        target_url = _norm_url(brief.get("app_url", "")) if brief.get("app_url") else None
    else:
        return err_response(
            "invalid_argument",
            "Provide release_id or app_url",
        ).model_dump()

    if not target_url:
        return err_response("invalid_argument", "Could not resolve app_url").model_dump()

    full = await _resources_tools().journeys_resource(app_url=target_url)
    filtered = list(full["journeys"])
    stale_gating = sum(
        1 for j in filtered if j.get("stale_recording") and j.get("criticality_class") in ("revenue", "activation")
    )
    j_hint = (
        f"{stale_gating} release-gating journey(s) look stale — refresh with record_test_flow before gating."
        if stale_gating
        else "Journeys look fresh enough for replay; run run_release_check when ready."
    )
    dto = JourneyListDTO(
        app_url=target_url,
        release_id=release_id,
        journeys=filtered,
        total=len(filtered),
        stale_release_gating_count=stale_gating,
        workflow_hint=full.get("workflow_hint", ""),
        resource_links={"all_journeys": "blop://journeys"},
        recommended_next_action_hint=j_hint,
    )
    payload = ok_response(dto.model_dump()).model_dump()
    _journeys_for_release_cache[cache_key] = (now, payload)
    return payload


async def get_release_and_journeys(release_id: str) -> dict:
    rel = await get_release_context(release_id)
    if not rel.get("ok"):
        return rel
    j = await get_journeys_for_release(release_id=release_id)
    if not j.get("ok"):
        return j
    batch = ReleaseAndJourneysDTO(
        release=ReleaseContextDTO.model_validate(rel["data"]),
        journeys=JourneyListDTO.model_validate(j["data"]),
    )
    return ok_response(batch.model_dump()).model_dump()


async def get_prd_and_acceptance_criteria(
    journey_id: Optional[str] = None,
    release_id: Optional[str] = None,
) -> dict:
    if journey_id and release_id:
        return err_response(
            "invalid_argument",
            "Pass only one of journey_id or release_id",
        ).model_dump()
    if not journey_id and not release_id:
        return err_response(
            "invalid_argument",
            "Provide journey_id or release_id",
        ).model_dump()

    if journey_id:
        flow = await sqlite.get_flow(journey_id)
        if not flow:
            return err_response("not_found", f"No recorded flow for journey_id={journey_id}").model_dump()
        ic = getattr(flow, "intent_contract", None)
        ic_dict = ic.model_dump() if ic is not None else None
        reqs: list[str] = []
        accept: list[str] = []
        if ic_dict:
            reqs.extend(ic_dict.get("success_assertions") or [])
            accept.extend(ic_dict.get("success_assertions") or [])
        if flow.assertions_json:
            accept.extend(flow.assertions_json)
        prd = PrdSummaryDTO(
            prd_source="recorded_flows",
            scope="journey",
            journey_id=journey_id,
            app_url=flow.app_url,
            key_requirements=list(dict.fromkeys(reqs))[:20],
            critical_journeys=[flow.flow_name],
            risk_notes=[],
            acceptance_criteria=list(dict.fromkeys(accept))[:30],
            flow_goal=flow.goal,
            intent_contract=ic_dict,
        )
        return ok_response(prd.model_dump()).model_dump()

    # release_id
    brief_raw = await sqlite.get_release_brief(release_id)
    if not brief_raw:
        prd = PrdSummaryDTO(
            prd_source="none",
            scope="release",
            release_id=release_id,
            key_requirements=[],
            critical_journeys=[],
            risk_notes=["No release brief yet — run run_release_check first."],
        )
        return ok_response(prd.model_dump()).model_dump()

    brief = ReleaseBrief.model_validate(brief_raw)
    app_url_norm = _norm_url(brief.app_url) if brief.app_url else ""
    critical_names = list(brief.blocker_journey_names or [])
    key_req: list[str] = []
    for flow in await sqlite.list_flows_full(app_url=brief.app_url or None):
        if app_url_norm and _norm_url(flow.app_url) != app_url_norm:
            continue
        if flow.intent_contract:
            key_req.extend(flow.intent_contract.success_assertions or [])
    prd = PrdSummaryDTO(
        prd_source="release_brief",
        scope="release",
        release_id=release_id,
        app_url=brief.app_url,
        key_requirements=list(dict.fromkeys(key_req))[:30],
        critical_journeys=critical_names[:20],
        risk_notes=[
            f"Decision: {brief.decision}",
            f"Blockers: {brief.blocker_count}",
        ],
        acceptance_criteria=[a.action for a in brief.top_actions[:10]],
        flow_goal=None,
        intent_contract=None,
    )
    return ok_response(prd.model_dump()).model_dump()


def _ux_taxonomy_payload() -> dict:
    dto = UxTaxonomyDTO(
        version="1",
        criticality_hints={
            "revenue": "Checkout, billing, paid conversion — gate failures as BLOCK.",
            "activation": "Signup, onboarding, first value — gate failures as BLOCK.",
            "retention": "Habit loops, return visits — usually INVESTIGATE on failure.",
            "support": "Help/support flows — informational unless policy says otherwise.",
            "other": "Exploratory coverage — not typically release-gating.",
        },
        archetype_hints={
            "saas_marketing": "Longer network idle / SPA settle; async marketing pages.",
            "editor_heavy": "Canvas/WebGL — allow extended settle; prefer goal_fallback when selectors drift.",
            "default": "Standard web app — hybrid replay is the default.",
        },
    )
    return ok_response(dto.model_dump()).model_dump()


async def get_ux_taxonomy(use_cache: bool = True) -> dict:
    global _ux_cache
    now = time.monotonic()
    if use_cache and _ux_cache and (now - _ux_cache[0]) < _CACHE_TTL_SECS:
        return _ux_cache[1]
    payload = _ux_taxonomy_payload()
    _ux_cache = (now, payload)
    return payload
