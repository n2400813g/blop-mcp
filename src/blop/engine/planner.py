"""Parse natural language commands into structured ExecutionIntent."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Literal, Optional
from urllib.parse import urlparse

from blop.config import BLOP_DISCOVERY_MAX_PAGES
from blop.schemas import ExecutionPlan, IntentContract


@dataclass
class ExecutionIntent:
    intent: Literal["discover", "record", "regress", "debug"]
    scope: Literal["public", "authed", "both"]
    app_url: str
    repo_path: str | None = None
    profile_name: str | None = None
    business_goal: str | None = None
    priorities: list[str] = field(default_factory=list)
    max_depth: int = 2
    max_pages: int = BLOP_DISCOVERY_MAX_PAGES
    run_mode: Literal["hybrid", "strict_steps", "goal_fallback"] = "hybrid"


def normalize_run_mode(raw_mode: str | None) -> Literal["hybrid", "strict_steps", "goal_fallback"]:
    """Map legacy aliases to the canonical run mode values used by replay."""
    mode = (raw_mode or "hybrid").strip().lower()
    if mode in {"strict", "strict_steps"}:
        return "strict_steps"
    if mode in {"goal", "goal_fallback", "fallback"}:
        return "goal_fallback"
    # "explore" is currently a hybrid replay variant with repair enabled.
    return "hybrid"


async def parse_command(
    command: str,
    app_url: str,
    repo_path: Optional[str] = None,
    profile_name: Optional[str] = None,
) -> ExecutionIntent:
    """Parse a natural language command string into a structured ExecutionIntent."""
    cmd_lower = (command or "").lower()

    # Determine intent
    if any(w in cmd_lower for w in ("discover", "find flows", "scan", "explore flows")):
        intent: Literal["discover", "record", "regress", "debug"] = "discover"
    elif any(w in cmd_lower for w in ("record", "capture", "save flow")):
        intent = "record"
    elif any(w in cmd_lower for w in ("debug", "diagnose", "investigate failure")):
        intent = "debug"
    else:
        intent = "regress"

    # Determine scope
    if any(w in cmd_lower for w in ("login", "auth", "dashboard", "account", "signed in", "logged in", "authed")):
        scope: Literal["public", "authed", "both"] = "authed"
    elif any(w in cmd_lower for w in ("public", "visitor", "anonymous", "unauthenticated")):
        scope = "public"
    else:
        scope = "both"

    # Determine run_mode once and normalize legacy aliases in one place.
    raw_run_mode: str | None = None
    if "strict" in cmd_lower:
        raw_run_mode = "strict"
    elif any(w in cmd_lower for w in ("goal fallback", "goal_fallback", "fallback to goal")):
        raw_run_mode = "goal_fallback"

    # Extract priorities
    priority_keywords = ("payment", "checkout", "signup", "registration", "onboarding", "pricing", "contact", "search")
    priorities = [kw for kw in priority_keywords if kw in cmd_lower]

    # Extract max_depth
    depth_match = re.search(r"depth[=:\s]+(\d+)", cmd_lower)
    max_depth = int(depth_match.group(1)) if depth_match else 2
    max_depth = max(1, min(max_depth, 5))

    pages_match = re.search(r"(?:max[_\s-]?pages|pages)[=:\s]+(\d+)", cmd_lower)
    max_pages = int(pages_match.group(1)) if pages_match else BLOP_DISCOVERY_MAX_PAGES
    max_pages = max(1, min(max_pages, 100))

    # Extract business goal from patterns like "goal: ..." or "focus on ..."
    business_goal: str | None = None
    for pat in (r"goal[:\s]+(.+?)(?:\.|$)", r"focus on[:\s]+(.+?)(?:\.|$)"):
        m = re.search(pat, cmd_lower)
        if m:
            business_goal = m.group(1).strip()
            break

    return ExecutionIntent(
        intent=intent,
        scope=scope,
        app_url=app_url,
        repo_path=repo_path,
        profile_name=profile_name,
        business_goal=business_goal,
        priorities=priorities,
        max_depth=max_depth,
        max_pages=max_pages,
        run_mode=normalize_run_mode(raw_run_mode),
    )


def _infer_goal_type(
    goal_text: str, target_surface: str
) -> Literal["navigation", "milestone", "transaction", "gate_check", "editor_panel", "exploration"]:
    lowered = (goal_text or "").lower()
    if any(token in lowered for token in ("upgrade", "pricing", "plan", "paywall", "billing")):
        return "gate_check"
    if target_surface == "editor":
        return "editor_panel"
    if any(token in lowered for token in ("checkout", "purchase", "buy", "payment", "submit order")):
        return "transaction"
    if any(token in lowered for token in ("explore", "scan", "discover")):
        return "exploration"
    if any(token in lowered for token in ("open", "enter", "navigate", "visit")):
        return "navigation"
    return "milestone"


def _infer_target_surface(
    goal_text: str, scope: str
) -> Literal["public_site", "authenticated_app", "editor", "billing", "settings", "unknown"]:
    lowered = (goal_text or "").lower()
    if any(token in lowered for token in ("editor", "canvas", "timeline", "captions", "ai agent")):
        return "editor"
    if any(token in lowered for token in ("pricing", "upgrade", "plan", "billing", "checkout")):
        return "billing"
    if any(token in lowered for token in ("settings", "account", "profile", "preferences")):
        return "settings"
    if scope == "public":
        return "public_site"
    if scope == "authed":
        return "authenticated_app"
    return "unknown"


def _extract_goal_urls(goal_text: str) -> list[str]:
    if not goal_text:
        return []
    matches = re.findall(r"https?://[^\s'\"),]+", goal_text)
    urls: list[str] = []
    for match in matches:
        cleaned = match.rstrip(".,;:")
        if cleaned and cleaned not in urls:
            urls.append(cleaned)
    return urls


def _infer_must_interact(goal_text: str, target_surface: str) -> list[str]:
    lowered = (goal_text or "").lower()
    actions = ["navigate"]
    if any(token in lowered for token in ("click", "open", "enter", "start", "create", "launch")):
        actions.append("click_primary")
    if "create" in lowered or "new project" in lowered:
        actions.append("click_create")
    if target_surface == "editor":
        actions.append("open_editor")
    if target_surface == "billing":
        actions.append("open_modal")
    return list(dict.fromkeys(actions))


def build_execution_plan(
    *,
    goal_text: str,
    app_url: str,
    command: str | None = None,
    profile_name: str | None = None,
    business_criticality: str = "other",
    planning_source: Literal[
        "nl_command", "explicit_goal", "discovery_flow", "baseline_recipe", "legacy_unstructured"
    ] = "explicit_goal",
    assertions: list[str] | None = None,
    run_mode: str | None = None,
) -> ExecutionPlan:
    intent = ExecutionIntent(intent="record", scope="both", app_url=app_url)
    if command:
        parsed = re.sub(r"\s+", " ", command).strip()
    else:
        parsed = ""
    if parsed:
        cmd_lower = parsed.lower()
        if any(w in cmd_lower for w in ("discover", "scan", "explore")):
            intent.intent = "discover"
        elif any(w in cmd_lower for w in ("debug", "diagnose", "investigate")):
            intent.intent = "debug"
        elif any(w in cmd_lower for w in ("replay", "regress", "release check")):
            intent.intent = "regress"
        if profile_name or any(w in cmd_lower for w in ("auth", "logged in", "dashboard", "editor", "account")):
            intent.scope = "authed"
        elif any(w in cmd_lower for w in ("public", "visitor", "unauthenticated")):
            intent.scope = "public"
        intent.run_mode = normalize_run_mode(run_mode or ("goal_fallback" if "goal fallback" in cmd_lower else None))
    else:
        intent.scope = "authed" if profile_name else "both"
        intent.run_mode = normalize_run_mode(run_mode)

    goal_urls = _extract_goal_urls(goal_text)
    app_host = (urlparse(app_url).netloc or "").lower()
    goal_hosts = {(urlparse(url).netloc or "").lower() for url in goal_urls if url}
    if goal_urls and app_host and goal_hosts and goal_hosts == {app_host} and not profile_name:
        intent.scope = "public"

    target_surface = _infer_target_surface(goal_text, intent.scope)
    required_assertions = [a for a in (assertions or []) if a]
    if not required_assertions and goal_text:
        required_assertions = [goal_text]
    fallback_policy: list[Literal["hybrid_repair", "goal_fallback", "hard_rerecord"]] = ["hybrid_repair"]
    intended_replay_mode = intent.run_mode
    if target_surface == "editor" and intended_replay_mode == "hybrid":
        intended_replay_mode = "strict_steps"
    if intended_replay_mode == "goal_fallback":
        fallback_policy.append("goal_fallback")

    expected_patterns = goal_urls[:] or [app_url]
    if target_surface == "editor":
        expected_patterns.append("/editor")
    elif target_surface == "billing":
        expected_patterns.extend(["/pricing", "/billing", "/plans"])
    elif target_surface == "settings":
        expected_patterns.extend(["/settings", "/account"])

    return ExecutionPlan(
        intent=intent.intent,
        goal_text=goal_text,
        effective_auth_expectation="authenticated"
        if intent.scope == "authed"
        else "anonymous"
        if intent.scope == "public"
        else "mixed",
        target_surface=target_surface,
        intended_replay_mode=intended_replay_mode,
        expected_landing_url_patterns=list(dict.fromkeys(expected_patterns)),
        required_assertion_phrases=required_assertions,
        fallback_policy=fallback_policy,
        planning_source=planning_source,
        scope=intent.scope,
        business_criticality=business_criticality
        if business_criticality in {"revenue", "activation", "retention", "support", "other"}
        else "other",
    )


def build_intent_contract(plan: ExecutionPlan) -> IntentContract:
    forbidden_shortcuts = ["agent_done_without_assertion"]
    if "goal_fallback" not in plan.fallback_policy:
        forbidden_shortcuts.append("goal_fallback_without_surface_match")
    return IntentContract(
        goal_text=plan.goal_text,
        goal_type=_infer_goal_type(plan.goal_text, plan.target_surface),
        target_surface=plan.target_surface,
        success_assertions=plan.required_assertion_phrases,
        must_interact=_infer_must_interact(plan.goal_text, plan.target_surface),
        forbidden_shortcuts=forbidden_shortcuts,
        scope=plan.scope,
        business_criticality=plan.business_criticality,
        planning_source=plan.planning_source,
        expected_url_patterns=plan.expected_landing_url_patterns,
        allowed_fallbacks=plan.fallback_policy,
    )
