from __future__ import annotations

from datetime import datetime, timezone
from urllib.parse import urlparse

from blop.schemas import (
    AuthBoundarySummary,
    ContextEdge,
    ContextGraphDiff,
    ContextGraphSummary,
    ContextImpactSummary,
    ContextNode,
    ImpactedJourney,
    JourneySummary,
    ReleaseScopeSummary,
    SiteContextGraph,
    SiteInventory,
)

CRITICAL_JOURNEY_TYPES = {"revenue", "activation"}
CRITICALITY_ORDER = {
    "revenue": 0,
    "activation": 1,
    "retention": 2,
    "support": 3,
    "other": 4,
}
STOP_SEGMENTS = {
    "",
    "app",
    "apps",
    "auth",
    "dashboard",
    "index",
    "new",
    "edit",
    "view",
    "home",
}


def detect_app_archetype(inventory: SiteInventory) -> str:
    text = " ".join(
        inventory.headings
        + [b.get("text", "") for b in inventory.buttons]
        + [link.get("text", "") for link in inventory.links]
        + inventory.routes
    ).lower()

    _canvas_signals = {"canvas", "timeline", "editor"}
    if any(k in text for k in _canvas_signals):
        return "editor_heavy"
    if any(k in text for k in ("checkout", "payment", "subscribe", "billing", "plans")):
        return "checkout_heavy"
    if any(k in text for k in ("pricing", "features", "contact", "about", "demo")) and len(inventory.routes) <= 8:
        return "marketing_site"
    return "saas_app"


def _route_confidence(route: str, business_signals: list[str], auth_signals: list[str]) -> float:
    r = route.lower()
    score = 0.45
    if any(sig.strip("/") in r for sig in business_signals):
        score += 0.25
    if any(sig.strip("/") in r for sig in auth_signals):
        score += 0.15
    if any(k in r for k in ("dashboard", "checkout", "billing", "project", "workspace", "settings")):
        score += 0.15
    return max(0.1, min(score, 0.95))


def _normalize_path(url_or_path: str | None, fallback: str = "/") -> str:
    if not url_or_path:
        return fallback
    parsed = urlparse(url_or_path)
    path = parsed.path or url_or_path or fallback
    if not path.startswith("/"):
        path = f"/{path.lstrip('/')}"
    return path or fallback


def _path_segments(path: str) -> list[str]:
    return [part for part in path.strip("/").split("/") if part]


def _route_area_key(route: str) -> str:
    parts = [seg.lower() for seg in _path_segments(route)]
    meaningful = [part for part in parts if part not in STOP_SEGMENTS and not part.isdigit()]
    for part in reversed(meaningful):
        if part:
            return part
    for part in parts:
        if part not in STOP_SEGMENTS and not part.isdigit():
            return part
    return "root"


def _route_auth_likelihood(route: str, auth_signals: list[str], has_profile: bool) -> str:
    route_lower = route.lower()
    public_markers = ("login", "signup", "register", "pricing", "contact", "about", "demo")
    protected_markers = ("dashboard", "billing", "settings", "workspace", "project", "checkout")
    if any(marker in route_lower for marker in public_markers):
        return "anonymous"
    if any(marker in route_lower for marker in protected_markers):
        return "authenticated"
    if has_profile and any(sig.strip("/") in route_lower for sig in auth_signals):
        return "authenticated"
    return "mixed"


def _flow_name_key(name: str) -> str:
    return "".join(ch.lower() if ch.isalnum() else "_" for ch in name).strip("_") or "unknown_journey"


def _flow_access(flow: object) -> dict:
    if isinstance(flow, dict):
        return flow
    if hasattr(flow, "model_dump"):
        return flow.model_dump()
    return {
        "flow_id": getattr(flow, "flow_id", None),
        "flow_name": getattr(flow, "flow_name", ""),
        "goal": getattr(flow, "goal", ""),
        "entry_url": getattr(flow, "entry_url", None),
        "app_url": getattr(flow, "app_url", ""),
        "business_criticality": getattr(flow, "business_criticality", "other"),
        "created_at": getattr(flow, "created_at", None),
    }


def _canonical_coverage_status(recorded_flow_ids: list[str], source_kinds: list[str]) -> str:
    if recorded_flow_ids:
        return "recorded"
    if "discovery_flow" in source_kinds:
        return "discovered_only"
    return "uncovered"


def _journey_sort_key(journey: JourneySummary) -> tuple[int, float, str]:
    return (
        CRITICALITY_ORDER.get(journey.business_criticality, 99),
        -journey.confidence,
        journey.label.lower(),
    )


def _build_journey_summaries(
    app_url: str,
    flows: list[dict],
    recorded_flows: list[object] | None,
    profile_name: str | None,
    now: str,
) -> list[JourneySummary]:
    by_key: dict[str, JourneySummary] = {}

    def _upsert(flow_like: object, source_kind: str) -> None:
        flow = _flow_access(flow_like)
        flow_name = flow.get("flow_name") or "unknown_journey"
        journey_key = _flow_name_key(flow_name)
        entry_route = _normalize_path(flow.get("starting_url") or flow.get("entry_url") or app_url)
        business_criticality = flow.get("business_criticality") or "other"
        auth_required = bool(profile_name) and any(
            token in entry_route.lower()
            for token in ("dashboard", "settings", "billing", "workspace", "project", "checkout")
        )
        confidence = float(flow.get("confidence", 0.82 if source_kind == "recorded_flow" else 0.62))
        confidence_reason = (
            "Recorded journey with stored flow coverage."
            if source_kind == "recorded_flow"
            else "Discovered journey inferred from crawl/planning output."
        )
        source_ref = flow.get("flow_id") or flow_name

        existing = by_key.get(journey_key)
        if existing is None:
            existing = JourneySummary(
                journey_key=journey_key,
                label=flow_name,
                goal=flow.get("goal", ""),
                business_criticality=business_criticality,
                auth_required=auth_required,
                entry_routes=[entry_route],
                coverage_status="uncovered",
                recorded_flow_ids=[],
                confidence=confidence,
                confidence_reason=confidence_reason,
                freshness_ts=now,
                observed_at=flow.get("created_at") or now,
                source_refs=[str(source_ref)],
                source_kinds=[source_kind],
            )
            by_key[journey_key] = existing
        else:
            if flow.get("goal") and (not existing.goal or len(flow.get("goal", "")) > len(existing.goal)):
                existing.goal = flow["goal"]
            existing.business_criticality = min(
                (existing.business_criticality, business_criticality),
                key=lambda value: CRITICALITY_ORDER.get(value, 99),
            )
            existing.auth_required = existing.auth_required or auth_required
            existing.confidence = max(existing.confidence, confidence)
            existing.freshness_ts = now
            if str(source_ref) not in existing.source_refs:
                existing.source_refs.append(str(source_ref))
            if source_kind not in existing.source_kinds:
                existing.source_kinds.append(source_kind)
            if entry_route not in existing.entry_routes:
                existing.entry_routes.append(entry_route)

        if source_kind == "recorded_flow" and flow.get("flow_id"):
            flow_id = str(flow["flow_id"])
            if flow_id not in existing.recorded_flow_ids:
                existing.recorded_flow_ids.append(flow_id)
        existing.coverage_status = _canonical_coverage_status(existing.recorded_flow_ids, existing.source_kinds)  # type: ignore[assignment]

    for flow in flows:
        _upsert(flow, "discovery_flow")
    for recorded_flow in recorded_flows or []:
        recorded_data = _flow_access(recorded_flow)
        if recorded_data.get("app_url") == app_url:
            _upsert(recorded_data, "recorded_flow")

    return sorted(by_key.values(), key=_journey_sort_key)


def _build_route_surfaces(
    inventory: SiteInventory,
    journeys: list[JourneySummary],
    profile_name: str | None,
    now: str,
) -> list[dict]:
    connected_by_route: dict[str, set[str]] = {}
    for journey in journeys:
        for route in journey.entry_routes:
            connected_by_route.setdefault(route, set()).add(journey.journey_key)

    route_surfaces: list[dict] = []
    for route in inventory.routes:
        connected = sorted(connected_by_route.get(route, set()))
        auth_likelihood = _route_auth_likelihood(route, inventory.auth_signals, bool(profile_name))
        route_surfaces.append(
            {
                "route": route,
                "area_key": _route_area_key(route),
                "auth_likelihood": auth_likelihood,
                "connected_journeys": connected,
                "confidence": _route_confidence(route, inventory.business_signals, inventory.auth_signals),
                "confidence_reason": "Derived from route patterns plus business/auth discovery signals.",
                "freshness_ts": now,
                "observed_at": now,
                "source_refs": [route],
                "source_kinds": ["crawl_inventory"],
            }
        )
    return route_surfaces


def _auth_boundary_summary(
    route_surfaces: list[dict], journeys: list[JourneySummary], profile_name: str | None
) -> AuthBoundarySummary:
    anonymous = sum(1 for route in route_surfaces if route["auth_likelihood"] == "anonymous")
    authenticated = sum(1 for route in route_surfaces if route["auth_likelihood"] == "authenticated")
    mixed = sum(1 for route in route_surfaces if route["auth_likelihood"] == "mixed")
    return AuthBoundarySummary(
        profile_name=profile_name,
        anonymous_routes=anonymous,
        authenticated_routes=authenticated,
        mixed_routes=mixed,
        auth_required_journeys=sum(1 for journey in journeys if journey.auth_required),
    )


def _decision_summary(
    route_surfaces: list[dict], journeys: list[JourneySummary], profile_name: str | None
) -> ContextGraphSummary:
    critical_journeys = [journey for journey in journeys if journey.business_criticality in CRITICAL_JOURNEY_TYPES]
    covered_critical = [journey for journey in critical_journeys if journey.coverage_status == "recorded"]
    uncovered_critical = [journey.label for journey in critical_journeys if journey.coverage_status != "recorded"]
    return ContextGraphSummary(
        route_surface_count=len(route_surfaces),
        journey_count=len(journeys),
        critical_journey_count=len(critical_journeys),
        covered_critical_journey_count=len(covered_critical),
        uncovered_critical_journeys=uncovered_critical,
        auth_boundary_summary=_auth_boundary_summary(route_surfaces, journeys, profile_name),
        top_journeys=journeys[:5],
    )


def build_context_graph(
    app_url: str,
    inventory: SiteInventory,
    flows: list[dict],
    profile_name: str | None = None,
    recorded_flows: list[object] | None = None,
) -> SiteContextGraph:
    now = datetime.now(timezone.utc).isoformat()
    archetype = detect_app_archetype(inventory)
    journeys = _build_journey_summaries(app_url, flows, recorded_flows, profile_name, now)
    route_surfaces = _build_route_surfaces(inventory, journeys, profile_name, now)
    summary = _decision_summary(route_surfaces, journeys, profile_name)

    nodes: list[ContextNode] = []
    edges: list[ContextEdge] = []

    for route_surface in route_surfaces:
        route = route_surface["route"]
        nodes.append(
            ContextNode(
                node_id=f"route:{route}",
                node_type="route",
                label=route,
                confidence=route_surface["confidence"],
                freshness_ts=now,
                metadata={
                    "path_depth": route.count("/"),
                    "route": route,
                    "area_key": route_surface["area_key"],
                    "auth_likelihood": route_surface["auth_likelihood"],
                    "connected_journeys": route_surface["connected_journeys"],
                    "source_kinds": route_surface["source_kinds"],
                    "source_refs": route_surface["source_refs"],
                    "confidence_reason": route_surface["confidence_reason"],
                },
            )
        )

    for journey in journeys:
        intent_id = f"intent:{journey.label}"
        nodes.append(
            ContextNode(
                node_id=intent_id,
                node_type="intent",
                label=journey.label,
                confidence=journey.confidence,
                freshness_ts=journey.freshness_ts,
                metadata={
                    "goal": journey.goal,
                    "business_criticality": journey.business_criticality,
                    "severity_if_broken": "blocker"
                    if journey.business_criticality in CRITICAL_JOURNEY_TYPES
                    else "high",
                    "journey_key": journey.journey_key,
                    "auth_required": journey.auth_required,
                    "entry_routes": journey.entry_routes,
                    "coverage_status": journey.coverage_status,
                    "recorded_flow_ids": journey.recorded_flow_ids,
                    "source_kinds": journey.source_kinds,
                    "source_refs": journey.source_refs,
                    "confidence_reason": journey.confidence_reason,
                },
            )
        )
        for entry_route in journey.entry_routes:
            edges.append(
                ContextEdge(
                    source_id=f"route:{entry_route}",
                    target_id=intent_id,
                    edge_type="supports_intent",
                    weight=1.0 if journey.coverage_status == "recorded" else 0.75,
                    confidence=min(0.98, journey.confidence + (0.1 if journey.coverage_status == "recorded" else 0.0)),
                    metadata={
                        "starting_url": entry_route,
                        "coverage_status": journey.coverage_status,
                        "journey_key": journey.journey_key,
                    },
                )
            )

    for link in inventory.links[:80]:
        href = (link.get("href") or "").strip()
        if not href:
            continue
        target_path = _normalize_path(href)
        source_path = _normalize_path(link.get("source_route") or link.get("source_url"), fallback="/")
        if target_path in inventory.routes and target_path != source_path:
            edges.append(
                ContextEdge(
                    source_id=f"route:{source_path}",
                    target_id=f"route:{target_path}",
                    edge_type="transitions_to",
                    weight=0.7,
                    confidence=0.5,
                    metadata={"anchor_text": link.get("text", ""), "source_kind": "crawl_inventory"},
                )
            )

    return SiteContextGraph(
        app_url=app_url,
        profile_name=profile_name,
        archetype=archetype,  # type: ignore[arg-type]
        created_at=now,
        nodes=nodes,
        edges=edges,
        metadata={
            "inventory_routes": len(inventory.routes),
            "inventory_forms": len(inventory.forms),
            "flow_count": len(flows),
            "recorded_flow_count": len(
                [flow for flow in recorded_flows or [] if _flow_access(flow).get("app_url") == app_url]
            ),
            "route_surfaces": route_surfaces,
            "journeys": [journey.model_dump() for journey in journeys],
            "decision_summary": summary.model_dump(),
        },
    )


def _journeys_from_graph(graph: SiteContextGraph) -> list[JourneySummary]:
    raw = (graph.metadata or {}).get("journeys")
    journeys: list[JourneySummary] = []
    if isinstance(raw, list):
        for item in raw:
            try:
                journeys.append(JourneySummary.model_validate(item))
            except Exception:
                continue
    if journeys:
        return sorted(journeys, key=_journey_sort_key)

    # Backward-compatible derivation for legacy graphs.
    for node in graph.nodes:
        if node.node_type != "intent":
            continue
        meta = node.metadata or {}
        journeys.append(
            JourneySummary(
                journey_key=meta.get("journey_key") or _flow_name_key(node.label),
                label=node.label,
                goal=meta.get("goal", ""),
                business_criticality=meta.get("business_criticality", "other"),
                auth_required=bool(meta.get("auth_required", False)),
                entry_routes=list(meta.get("entry_routes", [])),
                coverage_status=meta.get("coverage_status", "discovered_only"),
                recorded_flow_ids=list(meta.get("recorded_flow_ids", [])),
                confidence=node.confidence,
                confidence_reason=meta.get("confidence_reason"),
                freshness_ts=node.freshness_ts,
                observed_at=node.freshness_ts,
                source_refs=list(meta.get("source_refs", [])),
                source_kinds=list(meta.get("source_kinds", ["legacy_graph"])),
            )
        )
    return sorted(journeys, key=_journey_sort_key)


def _route_surfaces_from_graph(graph: SiteContextGraph) -> list[dict]:
    raw = (graph.metadata or {}).get("route_surfaces")
    if isinstance(raw, list) and raw:
        return raw
    route_surfaces: list[dict] = []
    for node in graph.nodes:
        if node.node_type != "route":
            continue
        meta = node.metadata or {}
        route_surfaces.append(
            {
                "route": meta.get("route", node.label),
                "area_key": meta.get("area_key", _route_area_key(node.label)),
                "auth_likelihood": meta.get("auth_likelihood", "mixed"),
                "connected_journeys": list(meta.get("connected_journeys", [])),
                "confidence": node.confidence,
                "confidence_reason": meta.get("confidence_reason"),
                "freshness_ts": node.freshness_ts,
                "observed_at": node.freshness_ts,
                "source_refs": list(meta.get("source_refs", [node.label])),
                "source_kinds": list(meta.get("source_kinds", ["legacy_graph"])),
            }
        )
    return route_surfaces


def get_context_graph_summary(graph: SiteContextGraph) -> ContextGraphSummary:
    raw = (graph.metadata or {}).get("decision_summary")
    if isinstance(raw, dict):
        try:
            return ContextGraphSummary.model_validate(raw)
        except Exception:
            pass
    route_surfaces = _route_surfaces_from_graph(graph)
    journeys = _journeys_from_graph(graph)
    return _decision_summary(route_surfaces, journeys, graph.profile_name)


def get_uncovered_critical_journeys(graph: SiteContextGraph) -> list[JourneySummary]:
    return [
        journey
        for journey in _journeys_from_graph(graph)
        if journey.business_criticality in CRITICAL_JOURNEY_TYPES and journey.coverage_status != "recorded"
    ]


def list_journey_summaries(graph: SiteContextGraph) -> list[JourneySummary]:
    return _journeys_from_graph(graph)


def find_journey_summary(
    graph: SiteContextGraph | None,
    *,
    flow_name: str | None = None,
    journey_key: str | None = None,
    flow_id: str | None = None,
) -> JourneySummary | None:
    if graph is None:
        return None
    journeys = _journeys_from_graph(graph)
    for journey in journeys:
        if journey_key and journey.journey_key == journey_key:
            return journey
        if flow_name and journey.label == flow_name:
            return journey
        if flow_id and flow_id in journey.recorded_flow_ids:
            return journey
    return None


def build_failure_neighborhood(
    graph: SiteContextGraph | None,
    *,
    flow_name: str | None = None,
    journey_key: str | None = None,
    flow_id: str | None = None,
) -> dict:
    journey = find_journey_summary(graph, flow_name=flow_name, journey_key=journey_key, flow_id=flow_id)
    if journey is None:
        return {
            "journey": None,
            "entry_routes": [],
            "business_criticality": "other",
            "auth_required": False,
            "coverage_status": "unknown",
            "areas": [],
        }
    areas = sorted({_route_area_key(route) for route in journey.entry_routes})
    return {
        "journey": journey.label,
        "journey_key": journey.journey_key,
        "entry_routes": journey.entry_routes,
        "business_criticality": journey.business_criticality,
        "auth_required": journey.auth_required,
        "coverage_status": journey.coverage_status,
        "areas": areas,
    }


def get_next_checks_for_release_scope(
    graph: SiteContextGraph | None,
    *,
    failed_journey_labels: list[str] | None = None,
    limit: int = 5,
) -> list[str]:
    if graph is None:
        return []
    journeys = _journeys_from_graph(graph)
    next_checks: list[str] = []
    failed_labels = set(failed_journey_labels or [])
    for journey in journeys:
        if failed_labels and journey.label not in failed_labels:
            continue
        if journey.auth_required:
            next_checks.append(f"Verify auth/session preconditions before rerunning {journey.label}.")
        if journey.entry_routes:
            next_checks.append(f"Re-check entry route {journey.entry_routes[0]} for {journey.label}.")
        if journey.coverage_status != "recorded" and journey.business_criticality in CRITICAL_JOURNEY_TYPES:
            next_checks.append(f"Record or refresh a release-gating flow for {journey.label}.")
    if not failed_labels:
        for journey in get_uncovered_critical_journeys(graph):
            next_checks.append(f"Add recorded coverage for critical journey {journey.label}.")
    deduped: list[str] = []
    for item in next_checks:
        if item not in deduped:
            deduped.append(item)
        if len(deduped) >= limit:
            break
    return deduped


def get_impacted_journeys(
    graph: SiteContextGraph,
    changed_files: list[str],
    changed_routes: list[str] | None = None,
    limit: int = 5,
) -> list[ImpactedJourney]:
    stopwords = {
        "src",
        "app",
        "lib",
        "pages",
        "components",
        "utils",
        "js",
        "ts",
        "tsx",
        "jsx",
        "index",
        "test",
        "spec",
        "stories",
        "style",
        "css",
        "scss",
        "mod",
        "pkg",
        "go",
        "py",
    }
    changed_segments: list[str] = []
    for path in changed_files:
        for part in path.replace("\\", "/").replace(".", "/").replace("_", "/").replace("-", "/").split("/"):
            lowered = part.lower()
            if len(lowered) >= 3 and lowered not in stopwords:
                changed_segments.append(lowered)
    for route in changed_routes or []:
        for part in _path_segments(route):
            lowered = part.lower()
            if len(lowered) >= 3:
                changed_segments.append(lowered)
    changed_segments = list(dict.fromkeys(changed_segments))

    weights = {
        "revenue": 1.0,
        "activation": 0.85,
        "retention": 0.7,
        "support": 0.55,
        "other": 0.35,
    }
    journeys = _journeys_from_graph(graph)
    impacted: list[ImpactedJourney] = []
    for journey in journeys:
        route_text = " ".join(journey.entry_routes).lower()
        label_text = f"{journey.label} {journey.goal}".lower()
        matched_segments = [segment for segment in changed_segments if segment in route_text or segment in label_text]
        if not matched_segments:
            continue
        match_score = float(len(matched_segments))
        coverage_bonus = 0.25 if journey.coverage_status == "recorded" else -0.05
        impact_score = round((match_score + coverage_bonus) * weights.get(journey.business_criticality, 0.35), 2)
        rationale = (
            f"Matched segments {matched_segments[:4]} across entry routes {journey.entry_routes[:3]} "
            f"for {journey.business_criticality} journey coverage={journey.coverage_status}."
        )
        impacted.append(
            ImpactedJourney(
                journey_key=journey.journey_key,
                label=journey.label,
                business_criticality=journey.business_criticality,
                coverage_status=journey.coverage_status,
                match_score=match_score,
                impact_score=impact_score,
                matched_segments=matched_segments[:6],
                entry_routes=journey.entry_routes[:5],
                rationale=rationale,
                flow_id=journey.recorded_flow_ids[0] if journey.recorded_flow_ids else None,
                flow_name=journey.label if journey.recorded_flow_ids else None,
                goal=journey.goal,
            )
        )
    impacted.sort(key=lambda item: (-item.impact_score, -item.match_score, item.label.lower()))
    return impacted[:limit]


def summarize_release_scope(previous: SiteContextGraph | None, current: SiteContextGraph) -> ReleaseScopeSummary:
    current_journeys = {journey.journey_key: journey for journey in _journeys_from_graph(current)}
    previous_journeys = {journey.journey_key: journey for journey in _journeys_from_graph(previous)} if previous else {}
    changed_journeys: list[str] = []
    newly_uncovered: list[str] = []

    for key, current_journey in current_journeys.items():
        previous_journey = previous_journeys.get(key)
        if previous_journey is None:
            changed_journeys.append(current_journey.label)
        else:
            if (
                current_journey.entry_routes != previous_journey.entry_routes
                or current_journey.coverage_status != previous_journey.coverage_status
                or current_journey.business_criticality != previous_journey.business_criticality
                or current_journey.auth_required != previous_journey.auth_required
            ):
                changed_journeys.append(current_journey.label)
            if previous_journey.coverage_status == "recorded" and current_journey.coverage_status != "recorded":
                newly_uncovered.append(current_journey.label)

    for key, previous_journey in previous_journeys.items():
        if key not in current_journeys:
            changed_journeys.append(previous_journey.label)
            if previous_journey.coverage_status == "recorded":
                newly_uncovered.append(previous_journey.label)

    previous_auth = get_context_graph_summary(previous).auth_boundary_summary if previous else AuthBoundarySummary()
    current_auth = get_context_graph_summary(current).auth_boundary_summary
    auth_changed = previous_auth.model_dump() != current_auth.model_dump()

    top_impacted = sorted(
        [journey for journey in current_journeys.values() if journey.label in set(changed_journeys)],
        key=_journey_sort_key,
    )[:5]

    return ReleaseScopeSummary(
        previous_graph_id=previous.graph_id if previous else None,
        current_graph_id=current.graph_id,
        changed_journeys=sorted(dict.fromkeys(changed_journeys)),
        newly_uncovered_journeys=sorted(dict.fromkeys(newly_uncovered)),
        auth_boundary_changed=auth_changed,
        top_impacted_journeys=top_impacted,
    )


def build_impact_summary(
    previous: SiteContextGraph | None,
    current: SiteContextGraph,
    impact_lens: list[str] | None = None,
) -> list[ContextImpactSummary]:
    release_scope = summarize_release_scope(previous, current)
    current_journeys = {journey.label: journey for journey in _journeys_from_graph(current)}
    lens = impact_lens or ["revenue", "activation"]
    out: list[ContextImpactSummary] = []
    for criticality in lens:
        changed = [
            name
            for name in release_scope.changed_journeys
            if current_journeys.get(name) and current_journeys[name].business_criticality == criticality
        ]
        uncovered = [
            name
            for name in release_scope.newly_uncovered_journeys
            if current_journeys.get(name) and current_journeys[name].business_criticality == criticality
        ]
        score = (
            (len(changed) * 18.0)
            + (len(uncovered) * 22.0)
            + (10.0 if release_scope.auth_boundary_changed and criticality in CRITICAL_JOURNEY_TYPES else 0.0)
        )
        risk_level = "blocker" if score >= 70 else "high" if score >= 40 else "medium" if score >= 15 else "low"
        out.append(
            ContextImpactSummary(
                criticality=criticality,  # type: ignore[arg-type]
                risk_level=risk_level,  # type: ignore[arg-type]
                affected_journeys=len(changed),
                changed_journeys=changed,
                newly_uncovered_journeys=uncovered,
            )
        )
    return out


def editor_hints_from_archetype(archetype: str) -> dict:
    if archetype != "editor_heavy":
        return {}
    return {
        "is_editor_heavy": True,
        "editor_settle_ms": 8000,
        "settle_ms": 5000,
        "push_state_navigation": True,
    }


def diff_context_graph(previous: SiteContextGraph | None, current: SiteContextGraph) -> ContextGraphDiff:
    if previous is None:
        return ContextGraphDiff(
            app_url=current.app_url,
            previous_graph_id=None,
            current_graph_id=current.graph_id,
            added_nodes=[n.node_id for n in current.nodes],
            removed_nodes=[],
            added_edges=[f"{e.source_id}->{e.target_id}:{e.edge_type}" for e in current.edges],
            removed_edges=[],
            confidence_delta=0.0,
        )

    prev_nodes = {n.node_id for n in previous.nodes}
    curr_nodes = {n.node_id for n in current.nodes}
    prev_edges = {f"{e.source_id}->{e.target_id}:{e.edge_type}" for e in previous.edges}
    curr_edges = {f"{e.source_id}->{e.target_id}:{e.edge_type}" for e in current.edges}

    prev_conf = sum(n.confidence for n in previous.nodes) / max(len(previous.nodes), 1)
    curr_conf = sum(n.confidence for n in current.nodes) / max(len(current.nodes), 1)

    return ContextGraphDiff(
        app_url=current.app_url,
        previous_graph_id=previous.graph_id,
        current_graph_id=current.graph_id,
        added_nodes=sorted(curr_nodes - prev_nodes),
        removed_nodes=sorted(prev_nodes - curr_nodes),
        added_edges=sorted(curr_edges - prev_edges),
        removed_edges=sorted(prev_edges - curr_edges),
        confidence_delta=round(curr_conf - prev_conf, 4),
    )
