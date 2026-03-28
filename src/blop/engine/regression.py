"""Flow replay engine — step-by-step hybrid replay with agent repair fallback."""

from __future__ import annotations

import asyncio
import base64
import inspect
import json
import os
import re
import time
from typing import TYPE_CHECKING, Awaitable, Callable, Optional
from urllib.parse import urlparse

from blop.engine.browser_pool import BROWSER_POOL
from blop.engine.evidence_policy import cap_artifact_paths, resolve_evidence_policy, should_capture_screenshot
from blop.engine.logger import get_logger
from blop.schemas import FailureCase, RecordedFlow, ReplayStepResult, ReplayTrace, StabilityFingerprint
from blop.storage import files as file_store

_log = get_logger("regression")

if TYPE_CHECKING:
    from playwright.async_api import Page


from blop.config import BLOP_AUTO_HEAL_MAX_BEHAVIOR_RISK as AUTO_HEAL_MAX_BEHAVIOR_RISK
from blop.config import BLOP_AUTO_HEAL_MIN_CONFIDENCE as AUTO_HEAL_MIN_CONFIDENCE
from blop.config import BLOP_REPLAY_CONCURRENCY, BLOP_STEP_TIMEOUT_SECS
from blop.schemas import DriftSummary


async def _normalize_page_text(value: object, *, max_depth: int = 2) -> str:
    """Resolve loosely mocked async return values into plain text."""
    current = value
    depth = 0
    while inspect.isawaitable(current) and depth < max_depth:
        current = await current
        depth += 1
    if current is None:
        return ""
    if isinstance(current, str):
        return current
    return str(current)


def _selector_entropy(selector: Optional[str]) -> float:
    if not selector:
        return 1.0
    # Heuristic: deep CSS chains and nth-child patterns are more brittle.
    depth = selector.count(" ") + selector.count(">")
    brittle_tokens = sum(selector.count(t) for t in ("nth-child", ":has(", ":nth-of-type", "[class*="))
    score = min(1.0, (depth * 0.08) + (brittle_tokens * 0.18))
    return round(score, 4)


def _aria_consistency(step) -> float:
    score = 0.0
    if getattr(step, "aria_role", None):
        score += 0.35
    if getattr(step, "aria_name", None):
        score += 0.35
    if getattr(step, "label_text", None):
        score += 0.2
    if getattr(step, "testid_selector", None):
        score += 0.1
    return round(min(1.0, score), 4)


def _required_heal_confidence(action: str | None, selector_entropy: float) -> float:
    required = float(AUTO_HEAL_MIN_CONFIDENCE)
    action_penalties = {
        "click": 0.05,
        "fill": 0.08,
        "select": 0.08,
        "upload": 0.1,
        "drag": 0.12,
    }
    required += action_penalties.get((action or "").lower(), 0.0)
    if selector_entropy >= 0.45:
        required += 0.05
    return round(min(0.98, required), 4)


def _allowed_heal_behavior_risk(action: str | None) -> float:
    allowed = float(AUTO_HEAL_MAX_BEHAVIOR_RISK)
    action_penalties = {
        "fill": 0.05,
        "select": 0.05,
        "upload": 0.08,
        "drag": 0.1,
    }
    allowed -= action_penalties.get((action or "").lower(), 0.0)
    return round(max(0.05, allowed), 4)


def _should_auto_heal(
    repair_confidence: float,
    behavior_risk: float,
    *,
    action: str | None = None,
    selector_entropy: float = 0.0,
) -> bool:
    return repair_confidence >= _required_heal_confidence(
        action, selector_entropy
    ) and behavior_risk <= _allowed_heal_behavior_risk(action)


async def _save_navigation_health_event(
    run_id: str,
    case_id: str,
    flow: RecordedFlow,
    expected_url: str,
    landed_url: str,
    page_title: str,
) -> None:
    from blop.storage import sqlite

    lowered = (landed_url or "").lower()
    auth_redirect_detected = any(token in lowered for token in ("/login", "/signin", "/sign-in", "/auth", "oauth"))
    await sqlite.save_run_health_event(
        run_id,
        "auth_landing_observed",
        {
            "case_id": case_id,
            "flow_id": flow.flow_id,
            "flow_name": flow.flow_name,
            "entry_area_key": _flow_entry_area_key(flow),
            "expected_url": expected_url,
            "landed_url": landed_url,
            "page_title": page_title,
            "auth_redirect_detected": auth_redirect_detected,
            "landed_authenticated": not auth_redirect_detected,
        },
    )


def _infer_surface_from_url(url: str, *, expected_surface: str | None = None) -> str:
    lowered = (url or "").lower()
    parsed = urlparse(url or "")
    path = (parsed.path or "/").lower()
    segments = [segment for segment in path.split("/") if segment]
    public_markers = {
        "pages",
        "reference",
        "docs",
        "blog",
        "help",
        "support",
        "challenges",
        "tutorial",
        "tutorials",
        "guide",
        "guides",
        "about",
        "contact",
        "examples",
    }
    app_markers = {"dashboard", "workspace", "project", "projects", "console", "admin"}
    if "/editor" in lowered:
        return "editor"
    if any(token in lowered for token in ("/pricing", "/billing", "/plans", "/upgrade")):
        return "billing"
    if any(token in lowered for token in ("/settings", "/account", "/profile")):
        return "settings"
    if any(token in lowered for token in ("/login", "/signin", "/auth", "oauth")):
        return "public_site"
    if path in {"", "/"}:
        return "public_site"
    if any(segment in public_markers for segment in segments):
        return "public_site"
    if expected_surface == "public_site" and not any(segment in app_markers for segment in segments):
        return "public_site"
    if lowered:
        return "authenticated_app"
    return "unknown"


def _has_success_assertion_match(assertion_results: list[dict], required_assertions: list[str]) -> bool | None:
    if not required_assertions:
        return None
    passed = [str(item.get("assertion", "")).lower() for item in assertion_results if item.get("passed")]
    if not passed:
        return False
    for required in required_assertions:
        required_lower = required.lower()
        if any(required_lower in candidate or candidate in required_lower for candidate in passed):
            return True
    return False


def _build_drift_summary(
    *,
    flow: RecordedFlow,
    status: str,
    replay_mode: str,
    assertion_results: list[dict],
    failure_reason_codes: list[str],
    rerecorded: bool,
    actual_landing_url: str | None = None,
) -> DriftSummary:
    intent = getattr(flow, "intent_contract", None)
    if intent is None:
        return DriftSummary(
            drift_detected=True,
            drift_types=["legacy_unstructured"],
            plan_fidelity="low",
            notes=["Flow has no persisted intent contract; replay trust is reduced."],
        )

    drift_types: list[str] = []
    allowed_fallback_used: list[str] = []
    disallowed_fallback_used: list[str] = []
    notes: list[str] = []

    actual_surface = _infer_surface_from_url(
        actual_landing_url or "",
        expected_surface=intent.target_surface,
    )
    surface_match = None
    if actual_landing_url:
        surface_match = actual_surface == intent.target_surface or (
            intent.target_surface == "authenticated_app"
            and actual_surface in {"authenticated_app", "editor", "billing", "settings"}
        )
        if not surface_match:
            drift_types.append("surface_drift")

    assertion_match = _has_success_assertion_match(assertion_results, intent.success_assertions)
    if assertion_match is False and status == "pass":
        drift_types.append("assertion_drift")

    if replay_mode == "goal_fallback":
        if "goal_fallback" in intent.allowed_fallbacks:
            allowed_fallback_used.append("goal_fallback")
            notes.append("Goal fallback was used as an allowed execution strategy.")
        else:
            disallowed_fallback_used.append("goal_fallback")
            drift_types.append("plan_drift")
    if rerecorded:
        if "hard_rerecord" in intent.allowed_fallbacks:
            allowed_fallback_used.append("hard_rerecord")
        else:
            disallowed_fallback_used.append("hard_rerecord")
            drift_types.append("plan_drift")
    if replay_mode in {"hybrid_repair", "agent_repair"} or "repair_rejected" in failure_reason_codes:
        if "hybrid_repair" in intent.allowed_fallbacks:
            allowed_fallback_used.append("hybrid_repair")
        else:
            disallowed_fallback_used.append("hybrid_repair")
            drift_types.append("repair_drift")
    if any(code in failure_reason_codes for code in ("auth_redirect", "auth_expired")):
        drift_types.append("auth_drift")

    plan_fidelity = "high"
    if disallowed_fallback_used or "surface_drift" in drift_types or "auth_drift" in drift_types:
        plan_fidelity = "low"
    elif allowed_fallback_used or assertion_match is False:
        plan_fidelity = "medium"

    return DriftSummary(
        drift_detected=bool(drift_types or allowed_fallback_used or disallowed_fallback_used),
        drift_types=list(dict.fromkeys(drift_types)),
        allowed_fallback_used=list(dict.fromkeys(allowed_fallback_used)),
        disallowed_fallback_used=list(dict.fromkeys(disallowed_fallback_used)),
        surface_match=surface_match,
        assertion_match=assertion_match,
        plan_fidelity=plan_fidelity,
        intended_surface=intent.target_surface,
        actual_surface=actual_surface if actual_landing_url else None,
        notes=notes,
    )


# ---------------------------------------------------------------------------
# Hybrid step-by-step executor
# ---------------------------------------------------------------------------


async def execute_recorded_flow(
    flow: RecordedFlow,
    run_id: str,
    case_id: str,
    storage_state: Optional[str],
    headless: bool = True,
    run_mode: str = "hybrid",
) -> FailureCase:
    """Replay a RecordedFlow step-by-step; repair broken selectors before falling back to agent."""
    from blop.engine.browser import make_browser_profile

    trace = ReplayTrace(
        flow_id=flow.flow_id,
        flow_name=flow.flow_name,
        run_mode="strict_steps",
    )

    browser_profile = make_browser_profile(headless=headless, storage_state=storage_state)
    evidence_policy = resolve_evidence_policy()
    video_dir = None
    if evidence_policy.video:
        video_dir = str(file_store._runs_dir() / "videos" / run_id)
        os.makedirs(video_dir, exist_ok=True)

    lease = await BROWSER_POOL.acquire(
        headless=bool(browser_profile.headless),
        storage_state=storage_state,
        record_video_dir=video_dir,
        record_video_size={"width": 1280, "height": 720} if video_dir else None,
    )
    context = lease.context
    page = lease.page

    # Apply any registered network route mocks
    from blop.tools.network import apply_routes_to_context

    await apply_routes_to_context(context)

    # Start Playwright tracing for debugging artifacts
    trace_zip = file_store.trace_path(run_id, case_id)
    tracing_enabled = False
    if evidence_policy.trace:
        try:
            await context.tracing.start(screenshots=True, snapshots=True, sources=False)
            tracing_enabled = True
        except Exception:
            _log.debug("tracing start failed", exc_info=True)

    # Capture console errors
    page.on("console", lambda msg: trace.console_errors.append(msg.text) if msg.type == "error" else None)
    page.on(
        "response",
        lambda resp: trace.network_errors.append(f"{resp.status} {resp.url}") if resp.status >= 500 else None,
    )

    try:
        deferred_asserts: list[tuple[int, object]] = []  # (step_idx, step) for assert steps
        landing_observed = False

        spa_hints = getattr(flow, "spa_hints", None)

        for step_idx, step in enumerate(flow.steps):
            # Cooperative cancellation boundary between potentially long browser steps.
            await asyncio.sleep(0)
            if step.action == "assert":
                deferred_asserts.append((step_idx, step))
                continue

            try:
                step_result = await asyncio.wait_for(
                    _execute_single_step(
                        page=page,
                        step=step,
                        step_idx=step_idx,
                        run_id=run_id,
                        case_id=case_id,
                        run_mode=run_mode,
                        trace=trace,
                        spa_hints=spa_hints,
                        flow_name=flow.flow_name,
                        flow_goal=flow.goal,
                        evidence_policy=evidence_policy,
                    ),
                    timeout=float(BLOP_STEP_TIMEOUT_SECS),
                )
            except asyncio.TimeoutError:
                step_result = ReplayStepResult(
                    step_id=step.step_id,
                    action=step.action,
                    status="fail",
                    replay_mode="step_timeout",
                    error=f"Step exceeded BLOP_STEP_TIMEOUT_SECS={BLOP_STEP_TIMEOUT_SECS}",
                    elapsed_ms=int(float(BLOP_STEP_TIMEOUT_SECS) * 1000),
                    failure_reason="step_timeout",
                )
            trace.step_results.append(step_result)

            try:
                from blop.reporting.health_event_taxonomy import canonical_replay_step_activity
                from blop.storage import sqlite as _sqlite_health

                await _sqlite_health.save_run_health_event(
                    run_id,
                    "replay_step_completed",
                    {
                        "case_id": case_id,
                        "flow_id": flow.flow_id,
                        "flow_name": flow.flow_name,
                        "step_id": step.step_id,
                        "step_index": step_idx,
                        "action": step.action,
                        "status": step_result.status,
                        "replay_mode": step_result.replay_mode,
                        "elapsed_ms": step_result.elapsed_ms,
                        "failure_reason": step_result.failure_reason,
                        "selector_entropy": step_result.selector_entropy,
                        "aria_consistency": step_result.aria_consistency,
                        "activity": canonical_replay_step_activity(step.action, step_result.status),
                    },
                )
            except Exception:
                _log.debug("replay_step_completed health event failed", exc_info=True)

            # Collect performance metrics after navigation steps
            if step.action == "navigate" and step_result.status == "pass":
                if not landing_observed:
                    try:
                        current_url = page.url
                        page_title = await _normalize_page_text(page.title())
                        await _save_navigation_health_event(
                            run_id=run_id,
                            case_id=case_id,
                            flow=flow,
                            expected_url=step.value or flow.entry_url or flow.app_url,
                            landed_url=current_url,
                            page_title=page_title[:120],
                        )
                        trace.landing_url = current_url
                        landing_observed = True
                    except Exception:
                        _log.debug("navigation health event failed", exc_info=True)
                try:
                    from blop.engine.performance import collect_performance_metrics

                    perf = await collect_performance_metrics(page)
                    if perf:
                        perf["step_id"] = step.step_id
                        perf["url"] = step.value or step.description
                        trace.performance_metrics.append(perf)
                except Exception:
                    _log.debug("collect performance metrics failed", exc_info=True)

            if step_result.status == "fail":
                if trace.step_failure_index is None:
                    trace.step_failure_index = step_idx
                if not step_result.screenshot_path and should_capture_screenshot(evidence_policy, "failure"):
                    failure_shot = await _take_step_screenshot(
                        page,
                        run_id,
                        case_id,
                        step_idx,
                        evidence_policy=evidence_policy,
                        trigger="failure",
                    )
                    if failure_shot:
                        step_result.screenshot_path = failure_shot
                if run_mode == "strict_steps":
                    break

            if step_result.screenshot_path:
                trace.screenshots.append(step_result.screenshot_path)

        # Tiered assertion evaluation: deterministic first, vision batch for semantic only
        if deferred_asserts:
            await asyncio.sleep(0)
            assertion_eval_results = await _evaluate_assertions(page, deferred_asserts)
            for result in assertion_eval_results:
                step_obj = result["step"]
                passed = result["passed"]
                eval_type = result["eval_type"]
                trace.assertion_results.append(
                    {
                        "assertion": step_obj.value or step_obj.description,
                        "passed": passed,
                        "eval_type": eval_type,
                        **({"failed": True} if not passed else {}),
                    }
                )
                trace.step_results.append(
                    ReplayStepResult(
                        step_id=step_obj.step_id,
                        action="assert",
                        status="pass" if passed else "fail",
                        replay_mode=eval_type,
                    )
                )

        # Take final screenshot
        try:
            if should_capture_screenshot(evidence_policy, "final"):
                final_path = file_store.screenshot_path(run_id, case_id, 999)
                await page.screenshot(path=final_path)
                trace.screenshots.append(final_path)
        except Exception:
            _log.debug("final screenshot failed", exc_info=True)

    finally:
        if tracing_enabled:
            try:
                await context.tracing.stop(path=trace_zip)
                trace.trace_path = trace_zip
            except Exception:
                _log.debug("tracing stop failed", exc_info=True)
        try:
            await lease.close()
        except Exception:
            _log.debug("context close failed", exc_info=True)

    trace.screenshots = cap_artifact_paths(trace.screenshots, limit=evidence_policy.artifact_cap)

    result_case = _trace_to_failure_case(trace, flow, run_id, case_id)
    if result_case.status == "blocked" and storage_state:
        from blop.engine.auth import invalidate_validated_session_cache

        invalidate_validated_session_cache(storage_state_path=storage_state)

    # Soft heal: persist healed selectors back into the recorded flow
    if result_case.healed_steps:
        from blop.storage.sqlite import update_flow_step_selector

        for hs in result_case.healed_steps:
            updates: dict = {}
            if hs.healed_locator_type == "role" and hs.healed_role and hs.healed_name:
                updates["aria_role"] = hs.healed_role
                updates["aria_name"] = hs.healed_name
            elif hs.healed_locator_type == "label" and hs.healed_name:
                updates["label_text"] = hs.healed_name
            elif hs.healed_locator_type == "text" and hs.healed_name:
                updates["target_text"] = hs.healed_name
            elif hs.healed_selector:
                updates["selector"] = hs.healed_selector
            if updates:
                try:
                    await update_flow_step_selector(flow.flow_id, hs.step_id, updates)
                except Exception:
                    _log.debug("update flow step selector failed", exc_info=True)

    return result_case


async def _execute_single_step(
    page: "Page",
    step,
    step_idx: int,
    run_id: str,
    case_id: str,
    run_mode: str,
    trace: ReplayTrace,
    spa_hints=None,
    flow_name: str | None = None,
    flow_goal: str | None = None,
    evidence_policy=None,
) -> ReplayStepResult:
    """Tiered fallback: testid → aria_role → by_label → CSS → text → agent repair."""
    from blop.engine.interaction import click_locator, fill_locator, wait_for_spa_ready

    step_start = time.perf_counter()
    action = step.action
    selector = step.selector
    value = step.value
    target_text = step.target_text
    base_selector_entropy = _selector_entropy(selector)
    base_aria_consistency = _aria_consistency(step)

    def _result(
        *,
        status: str,
        replay_mode: str,
        error: str | None = None,
        screenshot_path: str | None = None,
        retry_count: int = 0,
        repair_confidence: float = 0.0,
        failure_reason: str | None = None,
        healed_selector: str | None = None,
        healed_locator_type: str | None = None,
        healed_role: str | None = None,
        healed_name: str | None = None,
    ) -> ReplayStepResult:
        elapsed_ms = int((time.perf_counter() - step_start) * 1000)
        return ReplayStepResult(
            step_id=step.step_id,
            action=action,
            status=status,
            replay_mode=replay_mode,
            error=error,
            screenshot_path=screenshot_path,
            elapsed_ms=elapsed_ms,
            retry_count=retry_count,
            selector_entropy=base_selector_entropy,
            aria_consistency=base_aria_consistency,
            repair_confidence=repair_confidence,
            failure_reason=failure_reason,
            healed_selector=healed_selector,
            healed_locator_type=healed_locator_type,
            healed_role=healed_role,
            healed_name=healed_name,
        )

    # Auth redirect patterns — session expiry detected mid-run
    _AUTH_REDIRECT = ("/login", "/signin", "/sign-in", "/auth", "/account/login", "?redirect=")

    def _reason_from_exception(exc: Exception) -> str:
        text = str(exc).lower()
        if "timeout" in text:
            return "spa_not_ready"
        if "auth redirect detected" in text:
            return "auth_expired"
        if any(
            kw in text
            for kw in (
                "intercept",
                "outside of the viewport",
                "receives pointer events",
                "another element",
                "not visible",
            )
        ):
            return "click_intercepted"
        if "strict mode violation" in text or "resolved to" in text:
            return "ambiguous_locator"
        if "no node found" in text or "waiting for selector" in text or "not found" in text:
            return "locator_not_found"
        return "step_execution_failed"

    async def _pick_locator_candidate(locator, *, allow_ambiguous: bool = False):
        count = await locator.count()
        if count == 0:
            return None, "locator_not_found", None

        limit = min(count, 8)
        visible = []
        for idx in range(limit):
            candidate = locator.nth(idx)
            try:
                if await candidate.is_visible():
                    visible.append(candidate)
            except Exception:
                continue

        candidates = visible if visible else [locator.nth(idx) for idx in range(limit)]
        if len(candidates) == 1:
            return candidates[0], None, None
        if allow_ambiguous:
            return candidates[0], None, None
        return (
            None,
            "ambiguous_locator",
            f"Locator matched {count} elements; refusing first-match fallback",
        )

    async def _apply_action(locator):
        if action == "click":
            clicked = await click_locator(locator, timeout=5000, allow_force=True)
            if clicked:
                return True, None, None
            return False, "click_intercepted", "Click target was not actionable"
        if action == "fill":
            if not value:
                return False, "invalid_step", "Fill step missing value"
            filled = await fill_locator(locator, value, timeout=5000)
            if filled:
                return True, None, None
            return False, "fill_failed", "Could not fill target field"
        if action == "select":
            if not value:
                return False, "invalid_step", "Select step missing value"
            try:
                await locator.select_option(value)
                return True, None, None
            except Exception as exc:
                return False, _reason_from_exception(exc), str(exc)
        if action == "upload":
            if not value:
                return False, "invalid_step", "Upload step missing file path"
            try:
                await locator.set_input_files(value)
                return True, None, None
            except Exception as exc:
                return False, _reason_from_exception(exc), str(exc)
        if action == "drag":
            if not value:
                return False, "invalid_step", "Drag step missing drop selector"
            drop_target = page.locator(value)
            drop_count = await drop_target.count()
            if drop_count == 0:
                return False, "locator_not_found", "Drag drop target not found"
            drop_candidate, drop_reason, drop_error = await _pick_locator_candidate(drop_target)
            if not drop_candidate:
                return False, drop_reason, drop_error
            try:
                await locator.drag_to(drop_candidate)
                return True, None, None
            except Exception as exc:
                return False, _reason_from_exception(exc), str(exc)
        return False, "unsupported_action", f"Unsupported action: {action}"

    async def _try_locator(locator, replay_mode: str, allow_ambiguous_override: bool = False):
        # For click on navigation links (e.g. responsive sites have desktop + mobile nav
        # both in DOM), allow picking the first visible candidate rather than refusing.
        _allow_amb = allow_ambiguous_override or (
            action == "click" and step.aria_role in ("link", "button", "menuitem", "tab")
        )
        selected, reason, reason_error = await _pick_locator_candidate(locator, allow_ambiguous=_allow_amb)
        if not selected:
            return False, reason, reason_error, replay_mode
        ok, apply_reason, apply_error = await _apply_action(selected)
        if ok:
            return True, None, None, replay_mode
        return False, apply_reason, apply_error, replay_mode

    async def _try_replay_recipe():
        for candidate in getattr(step, "replay_recipe", []) or []:
            kind = str(candidate.get("kind") or "")
            try:
                if kind == "testid" and candidate.get("selector"):
                    ok, reason, err, replay_mode = await _try_locator(page.locator(candidate["selector"]), "testid")
                elif kind in ("role_exact", "role_fuzzy") and candidate.get("role") and candidate.get("name"):
                    ok, reason, err, replay_mode = await _try_locator(
                        page.get_by_role(
                            candidate["role"],
                            name=candidate["name"],
                            exact=(kind == "role_exact"),
                        ),
                        "aria_role_exact" if kind == "role_exact" else "aria_role",
                    )
                elif kind in ("label_exact", "label_fuzzy") and candidate.get("text") and action in ("fill", "upload"):
                    ok, reason, err, replay_mode = await _try_locator(
                        page.get_by_label(candidate["text"], exact=(kind == "label_exact")),
                        "by_label_exact" if kind == "label_exact" else "by_label",
                    )
                elif kind == "selector" and candidate.get("selector"):
                    ok, reason, err, replay_mode = await _try_locator(page.locator(candidate["selector"]), "selector")
                elif kind in ("text_exact", "text_fuzzy") and candidate.get("text"):
                    ok, reason, err, replay_mode = await _try_locator(
                        page.get_by_text(candidate["text"], exact=(kind == "text_exact")),
                        "text_lookup_exact" if kind == "text_exact" else "text_lookup",
                        allow_ambiguous_override=True,
                    )
                else:
                    continue
                if ok:
                    return True, None, None, replay_mode
                if reason and reason != "locator_not_found":
                    return False, reason, err, replay_mode
            except Exception:
                _log.debug("compiled replay recipe candidate failed", exc_info=True)
        return False, None, None, None

    last_reason: str | None = None
    last_error: str | None = None

    # Tier 0: Navigate steps
    if action == "navigate":
        try:
            nav_url = value or step.description
            # Retroactive editor-heavy detection: flows recorded before auto-detection was
            # added won't have spa_hints.is_editor_heavy set. Re-classify via context graph
            # archetype so they still get the extended canvas wait without re-recording.
            _effective_hints = spa_hints
            if spa_hints and not spa_hints.is_editor_heavy:
                from blop.engine.context_graph import detect_app_archetype, editor_hints_from_archetype
                from blop.schemas import SiteInventory

                _probe = SiteInventory(
                    app_url=nav_url or "",
                    routes=[nav_url or ""],
                    buttons=[],
                    links=[],
                    forms=[],
                    headings=[],
                    auth_signals=[],
                    business_signals=[],
                )
                _archetype = detect_app_archetype(_probe)
                _hint_kwargs = editor_hints_from_archetype(_archetype)
                if _hint_kwargs:
                    from blop.schemas import SpaHints as _SpaHints

                    _effective_hints = _SpaHints(**{**spa_hints.model_dump(), **_hint_kwargs})
            elif spa_hints is None:
                _effective_hints = None

            nav_timeout = 45000 if (_effective_hints and _effective_hints.is_editor_heavy) else 30000
            # domcontentloaded is the safe default — networkidle times out on SPAs that have
            # background polling/websockets. wait_for_spa_ready() below handles SPA settling.
            await page.goto(nav_url, wait_until="domcontentloaded", timeout=nav_timeout)
            # Detect silent auth redirect (session expired mid-run)
            _current = page.url.lower()
            if any(pat in _current for pat in _AUTH_REDIRECT):
                return _result(status="fail", replay_mode="selector", error=f"Auth redirect detected: {page.url}")
            # Wait for SPA to reach a usable state before continuing
            await wait_for_spa_ready(
                page,
                wait_for_selector=_effective_hints.wait_for_selector if _effective_hints else None,
                wait_for_shadow_selector=_effective_hints.wait_for_shadow_selector if _effective_hints else None,
                settle_ms=_effective_hints.settle_ms if _effective_hints else 1500,
                spa_hints=_effective_hints,
            )
            shot = await _take_step_screenshot(
                page, run_id, case_id, step_idx, evidence_policy=evidence_policy, trigger="navigation"
            )
            return _result(status="pass", replay_mode="selector", screenshot_path=shot)
        except Exception as e:
            return _result(
                status="fail",
                replay_mode="selector",
                error=str(e),
                failure_reason=_reason_from_exception(e),
            )

    # Explicit wait steps (supports numeric seconds in value)
    if action == "wait":
        try:
            wait_secs = float(value) if value else max(0.1, float(getattr(step, "wait_after_secs", 0.5)))
        except Exception:
            wait_secs = max(0.1, float(getattr(step, "wait_after_secs", 0.5)))
        await page.wait_for_timeout(int(wait_secs * 1000))
        shot = await _take_step_screenshot(page, run_id, case_id, step_idx, evidence_policy=evidence_policy)
        return _result(status="pass", replay_mode="wait", screenshot_path=shot)

    recipe_ok, recipe_reason, recipe_error, recipe_mode = await _try_replay_recipe()
    if recipe_ok:
        shot = await _take_step_screenshot(page, run_id, case_id, step_idx, evidence_policy=evidence_policy)
        return _result(status="pass", replay_mode=recipe_mode or "compiled_recipe", screenshot_path=shot)
    if recipe_reason:
        last_reason, last_error = recipe_reason, recipe_error

    # Tier 1: data-testid selector (most stable)
    testid_sel = getattr(step, "testid_selector", None)
    if testid_sel:
        try:
            ok, reason, err, _ = await _try_locator(page.locator(testid_sel), "testid")
            if ok:
                shot = await _take_step_screenshot(page, run_id, case_id, step_idx, evidence_policy=evidence_policy)
                return _result(status="pass", replay_mode="testid", screenshot_path=shot)
            last_reason, last_error = reason, err
        except Exception:
            _log.debug("testid locator try failed", exc_info=True)

    # Tier 2: ARIA role + name
    aria_role = getattr(step, "aria_role", None)
    aria_name = getattr(step, "aria_name", None)
    if aria_role and aria_name:
        try:
            for exact_mode in (True, False):
                loc = page.get_by_role(aria_role, name=aria_name, exact=exact_mode)
                ok, reason, err, _ = await _try_locator(
                    loc,
                    "aria_role_exact" if exact_mode else "aria_role_fuzzy",
                )
                if ok:
                    shot = await _take_step_screenshot(page, run_id, case_id, step_idx, evidence_policy=evidence_policy)
                    return _result(
                        status="pass",
                        replay_mode="aria_role_exact" if exact_mode else "aria_role",
                        screenshot_path=shot,
                    )
                if reason != "locator_not_found":
                    last_reason, last_error = reason, err
        except Exception:
            _log.debug("aria role locator try failed", exc_info=True)

    # Tier 3: by-label (fill actions only)
    label_text = getattr(step, "label_text", None)
    if action in ("fill", "upload") and label_text and value:
        try:
            for exact_mode in (True, False):
                loc = page.get_by_label(label_text, exact=exact_mode)
                ok, reason, err, _ = await _try_locator(
                    loc,
                    "by_label_exact" if exact_mode else "by_label",
                )
                if ok:
                    shot = await _take_step_screenshot(page, run_id, case_id, step_idx, evidence_policy=evidence_policy)
                    return _result(
                        status="pass",
                        replay_mode="by_label_exact" if exact_mode else "by_label",
                        screenshot_path=shot,
                    )
                if reason != "locator_not_found":
                    last_reason, last_error = reason, err
        except Exception:
            _log.debug("by_label locator try failed", exc_info=True)

    # Tier 4: CSS selector
    if selector:
        try:
            ok, reason, err, _ = await _try_locator(page.locator(selector), "selector")
            if ok:
                shot = await _take_step_screenshot(page, run_id, case_id, step_idx, evidence_policy=evidence_policy)
                return _result(status="pass", replay_mode="selector", screenshot_path=shot)
            if reason:
                last_reason, last_error = reason, err
        except Exception:
            _log.debug("selector locator try failed", exc_info=True)

    # Tier 5: Text-based lookup
    if target_text:
        try:
            for exact_mode in (True, False):
                text_loc = page.get_by_text(target_text, exact=exact_mode)
                ok, reason, err, _ = await _try_locator(
                    text_loc,
                    "text_lookup_exact" if exact_mode else "text_lookup_fuzzy",
                    allow_ambiguous_override=True,
                )
                if ok:
                    shot = await _take_step_screenshot(page, run_id, case_id, step_idx, evidence_policy=evidence_policy)
                    return _result(
                        status="pass",
                        replay_mode="text_lookup_exact" if exact_mode else "text_lookup",
                        screenshot_path=shot,
                    )
                if reason != "locator_not_found":
                    last_reason, last_error = reason, err
        except Exception:
            _log.debug("text lookup locator try failed", exc_info=True)

    # Tier 6: Hybrid repair via agent (ARIA-enhanced)
    if run_mode == "hybrid":
        trace.run_mode = "hybrid_repair"
        repair_result = await repair_step_with_agent(
            step,
            page,
            flow_name=flow_name,
            flow_goal=flow_goal,
            step_index=step_idx,
        )
        if repair_result and repair_result.get("_quota_error"):
            return _result(
                status="fail",
                replay_mode="agent_repair",
                error="Gemini quota/rate-limit exceeded",
                failure_reason="llm_quota_error",
            )
        if repair_result:
            repair_confidence = float(repair_result.get("repair_confidence", 0.6))
            behavior_risk = float(repair_result.get("behavior_risk", 0.35))
            selector_entropy = _selector_entropy(step.selector)
            required_confidence = _required_heal_confidence(step.action, selector_entropy)
            allowed_risk = _allowed_heal_behavior_risk(step.action)
            if not _should_auto_heal(
                repair_confidence,
                behavior_risk,
                action=step.action,
                selector_entropy=selector_entropy,
            ):
                return _result(
                    status="fail",
                    replay_mode="agent_repair",
                    error=(
                        "Repair proposed but not auto-applied (confidence/risk threshold not met): "
                        f"confidence={repair_confidence:.2f}, required={required_confidence:.2f}, "
                        f"risk={behavior_risk:.2f}, allowed={allowed_risk:.2f}"
                    ),
                    repair_confidence=repair_confidence,
                    failure_reason="repair_rejected",
                )
            locator_type = repair_result.get("repaired_locator_type", "css")
            repaired_selector = repair_result.get("repaired_selector")
            repaired_role = repair_result.get("repaired_role")
            repaired_name = repair_result.get("repaired_name")
            repaired_value = repair_result.get("repaired_value", value)
            repaired_action = repair_result.get("repaired_action", action)
            try:
                el = None
                if locator_type == "role" and repaired_role and repaired_name:
                    loc = page.get_by_role(repaired_role, name=repaired_name, exact=True)
                    el, _, _ = await _pick_locator_candidate(loc)
                    if el is None:
                        loc = page.get_by_role(repaired_role, name=repaired_name, exact=False)
                        el, _, _ = await _pick_locator_candidate(loc)
                elif locator_type == "label" and repaired_name:
                    loc = page.get_by_label(repaired_name, exact=False)
                    el, _, _ = await _pick_locator_candidate(loc)
                elif locator_type == "text" and repaired_name:
                    loc = page.get_by_text(repaired_name, exact=False)
                    el, _, _ = await _pick_locator_candidate(loc)
                elif repaired_selector:
                    loc = page.locator(repaired_selector)
                    el, _, _ = await _pick_locator_candidate(loc)

                if el:
                    original_action = action
                    original_value = value
                    try:
                        action = repaired_action
                        value = repaired_value
                        ok, repaired_reason, repaired_error = await _apply_action(el)
                        if not ok:
                            return _result(
                                status="fail",
                                replay_mode="agent_repair",
                                error=repaired_error or "Repaired action failed",
                                repair_confidence=repair_confidence,
                                failure_reason=repaired_reason or "repair_failed",
                            )
                    finally:
                        action = original_action
                        value = original_value
                else:
                    from blop.engine.vision import click_by_vision

                    desc = target_text or step.description
                    await click_by_vision(page, desc)

                shot = await _take_step_screenshot(page, run_id, case_id, step_idx, evidence_policy=evidence_policy)
                return _result(
                    status="repaired",
                    replay_mode="agent_repair",
                    screenshot_path=shot,
                    repair_confidence=repair_confidence,
                    healed_selector=repaired_selector,
                    healed_locator_type=locator_type,
                    healed_role=repaired_role,
                    healed_name=repaired_name,
                )
            except Exception as e:
                return _result(
                    status="fail",
                    replay_mode="agent_repair",
                    error=str(e),
                    repair_confidence=repair_confidence,
                    failure_reason=_reason_from_exception(e),
                )

    return _result(
        status="fail",
        replay_mode="selector",
        error=last_error or "No selector, text, or repair succeeded",
        failure_reason=last_reason or "locator_not_found",
    )


async def repair_step_with_agent(
    step,
    page: "Page",
    *,
    flow_name: str | None = None,
    flow_goal: str | None = None,
    step_index: int | None = None,
) -> Optional[dict]:
    """Send REPAIR_STEP_PROMPT + ARIA context + screenshot to the configured LLM; return repaired action dict."""
    from blop.engine.vision import _check_llm_api_key, _make_vision_message

    if not _check_llm_api_key():
        return None

    from blop.engine.llm_factory import ainvoke_llm, make_planning_llm
    from blop.prompts import REPAIR_STEP_PROMPT

    try:
        aria_tree = ""
        try:
            from blop.engine.snapshots import format_snapshot_for_llm

            snapshot = await page.accessibility.snapshot(interesting_only=True)
            if snapshot:
                nodes = _extract_interactive_nodes_flat(snapshot, max_nodes=30)
                if nodes:
                    aria_tree = format_snapshot_for_llm(nodes)
        except Exception:
            _log.debug("ARIA snapshot failed", exc_info=True)

        img_bytes = await page.screenshot(type="jpeg", quality=85)
        b64 = base64.b64encode(img_bytes).decode()
        current_url = page.url

        aria_section = f"\nAvailable interactive elements (ARIA):\n{aria_tree}\n" if aria_tree else ""

        from blop.engine.secrets import mask_text

        prompt = REPAIR_STEP_PROMPT.format(
            action=step.action,
            selector=step.selector or "none",
            target_text=step.target_text or "none",
            description=step.description,
            current_url=current_url,
            aria_section=aria_section,
        )
        prompt += _repair_flow_context_suffix(
            flow_name=flow_name,
            flow_goal=flow_goal,
            step_index=step_index,
            step=step,
        )
        prompt += (
            "\nReturn JSON with repair_confidence (0..1) and behavior_risk (0..1) "
            "in addition to any repaired locator/action fields."
        )
        prompt = mask_text(prompt)

        llm = make_planning_llm(temperature=0.2, max_output_tokens=300, role="repair")
        msg = _make_vision_message(prompt, b64)
        response = await ainvoke_llm(
            llm,
            [msg],
            span_name="blop.llm.repair_step",
            role="repair",
        )
        text = str(response.content) if hasattr(response, "content") else str(response)
        m = re.search(r"\{.*\}", text, re.DOTALL)
        if m:
            return json.loads(m.group())
    except Exception as e:
        _e = str(e).lower()
        if "429" in _e or "quota" in _e or "resource_exhausted" in _e or "rate" in _e:
            return {"_quota_error": True}

    return None


def _repair_flow_context_suffix(
    *,
    flow_name: str | None,
    flow_goal: str | None,
    step_index: int | None,
    step,
) -> str:
    parts: list[str] = []
    if flow_name:
        parts.append(f"Flow name: {flow_name}")
    if flow_goal:
        parts.append(f"Flow goal: {flow_goal}")
    if step_index is not None:
        parts.append(f"Current replay step index: {step_index}")
    if not getattr(step, "selector", None) and not getattr(step, "aria_name", None):
        parts.append(
            "This recorded step has weak locator data. Infer the intended target from the flow goal, "
            "the current page state, and the visible interactive elements."
        )
    if not parts:
        return ""
    return "\n\nReplay context:\n- " + "\n- ".join(parts)


from blop.engine.dom_utils import extract_interactive_nodes_flat as _extract_interactive_nodes_flat


async def _take_step_screenshot(
    page: "Page",
    run_id: str,
    case_id: str,
    step_idx: int,
    *,
    evidence_policy=None,
    trigger: str = "step",
) -> Optional[str]:
    if evidence_policy is not None and not should_capture_screenshot(evidence_policy, trigger):
        return None
    try:
        path = file_store.screenshot_path(run_id, case_id, step_idx)
        await page.screenshot(path=path)
        return path
    except Exception:
        return None


async def _evaluate_assertions(page: "Page", deferred_asserts: list) -> list[dict]:
    """Evaluate assertions using deterministic checks where possible, vision batch for semantic."""
    results = []
    semantic_batch: list[tuple] = []  # (step_obj, text)

    for _step_idx, step_obj in deferred_asserts:
        sa = getattr(step_obj, "structured_assertion", None)
        text = step_obj.value or step_obj.description

        if sa is None or sa.assertion_type == "semantic":
            semantic_batch.append((step_obj, text))
            continue

        try:
            passed = await _eval_deterministic(page, sa)
        except Exception:
            # Deterministic eval failed — fall back to vision
            semantic_batch.append((step_obj, text))
            continue

        results.append({"step": step_obj, "passed": passed, "eval_type": sa.assertion_type})

    # Batch evaluate remaining semantic assertions in a single vision call
    if semantic_batch:
        from blop.engine.vision import assert_all_by_vision

        texts = [t for _, t in semantic_batch]
        try:
            vision_results = await assert_all_by_vision(page, texts)
            for (step_obj, _), passed in zip(semantic_batch, vision_results):
                results.append({"step": step_obj, "passed": passed, "eval_type": "vision_batch"})
        except Exception as e:
            _e = str(e).lower()
            if "429" in _e or "quota" in _e or "resource_exhausted" in _e or "rate" in _e:
                # Quota/rate-limit error — mark assertions as failed (not silently passed)
                for step_obj, _ in semantic_batch:
                    results.append({"step": step_obj, "passed": False, "eval_type": "quota_error"})
            else:
                for step_obj, _ in semantic_batch:
                    results.append({"step": step_obj, "passed": False, "eval_type": "vision_batch"})

    return results


async def _eval_deterministic(page: "Page", sa) -> bool:
    """Evaluate a non-semantic StructuredAssertion without an LLM call."""
    t = sa.assertion_type
    target = sa.target
    expected = sa.expected

    if t == "text_present":
        if target:
            try:
                el_text = await page.locator(target).first.text_content(timeout=3000)
                result = (expected or "") in (el_text or "")
            except Exception:
                # Target not found — check full body text
                body = await page.evaluate("() => document.body.innerText")
                result = (expected or "") in (body or "")
        else:
            body = await page.evaluate("() => document.body.innerText")
            result = (expected or "") in (body or "")

    elif t == "element_visible":
        if target:
            result = await page.locator(target).first.is_visible(timeout=3000)
        else:
            result = False

    elif t == "url_contains":
        result = (expected or "") in page.url

    elif t == "page_title":
        title = await page.title()
        result = (expected or "") in title

    elif t == "count":
        if target and expected:
            count = await page.locator(target).count()
            try:
                result = count == int(expected)
            except ValueError:
                result = False
        else:
            result = False

    else:
        raise ValueError(f"Unknown deterministic type: {t}")

    return (not result) if sa.negated else result


def _trace_to_failure_case(
    trace: ReplayTrace,
    flow: RecordedFlow,
    run_id: str,
    case_id: str,
) -> FailureCase:
    """Convert a ReplayTrace to a FailureCase."""
    assertion_failures = [r["assertion"] for r in trace.assertion_results if not r.get("passed", True)]
    failed_steps = [r for r in trace.step_results if r.status == "fail"]
    failure_reason_codes = sorted({r.failure_reason for r in failed_steps if r.failure_reason})

    if trace.network_errors:
        status: str = "fail"
    elif assertion_failures or failed_steps:
        status = "fail"
    else:
        status = "pass"

    # Detect auth-blocked scenarios: check raw_result and step errors only.
    # Console errors are intentionally excluded — background resource 401s (analytics,
    # CDN, third-party APIs) fire on otherwise-authenticated pages and would produce
    # false positives. Actual auth redirects are caught by the navigate-step check
    # which sets step.error = "Auth redirect detected: <url>".
    auth_kws = ("401", "403", "unauthorized", "forbidden", "login required", "auth redirect detected")
    _auth_blocked = (
        ("auth_expired" in failure_reason_codes)
        or any(kw in trace.raw_result.lower() for kw in auth_kws)
        or any(r.error and any(kw in r.error.lower() for kw in auth_kws) for r in trace.step_results)
    )
    if _auth_blocked:
        status = "blocked"
        if not trace.raw_result:
            trace.raw_result = (
                "Session expired mid-run. Re-run after refreshing auth profile with capture_auth_session."
            )

    repro: list[str] = []
    for r in trace.step_results:
        if r.status in ("fail",):
            repro.append(f"Step {r.step_id} ({r.action}) failed via {r.replay_mode}: {r.error or 'unknown'}")

    fingerprints: list[StabilityFingerprint] = []
    repair_confidences: list[float] = []
    for r in trace.step_results:
        drift = min(1.0, max(0.0, (r.selector_entropy * 0.6) + (r.retry_count * 0.1) - (r.aria_consistency * 0.3)))
        fingerprints.append(
            StabilityFingerprint(
                selector_entropy=r.selector_entropy,
                aria_consistency=r.aria_consistency,
                latency_ms=r.elapsed_ms,
                retry_count=r.retry_count,
                drift_score=round(drift, 4),
            )
        )
        if r.repair_confidence > 0:
            repair_confidences.append(r.repair_confidence)

    avg_repair_confidence = round(sum(repair_confidences) / len(repair_confidences), 4) if repair_confidences else 0.0
    healing_decision = "none"
    if any(r.status == "repaired" for r in trace.step_results):
        healing_decision = "auto_heal"
    elif any((r.error or "").startswith("Repair proposed but not auto-applied") for r in trace.step_results):
        healing_decision = "propose_patch"

    from blop.schemas import HealedStep

    healed_steps: list[HealedStep] = []
    for r in trace.step_results:
        if r.status == "repaired" and (r.healed_selector or r.healed_role):
            original_sel = None
            for s in flow.steps:
                if s.step_id == r.step_id:
                    original_sel = s.selector
                    break
            healed_steps.append(
                HealedStep(
                    step_id=r.step_id,
                    original_selector=original_sel,
                    healed_selector=r.healed_selector,
                    healed_locator_type=r.healed_locator_type,
                    healed_role=r.healed_role,
                    healed_name=r.healed_name,
                    repair_confidence=r.repair_confidence,
                )
            )

    return FailureCase(
        case_id=case_id,
        run_id=run_id,
        flow_id=flow.flow_id,
        flow_name=flow.flow_name,
        status=status,
        severity="none",
        failure_reason_codes=failure_reason_codes,
        repro_steps=repro,
        console_errors=trace.console_errors[:20],
        network_errors=trace.network_errors[:20],
        screenshots=trace.screenshots,
        raw_result=trace.raw_result,
        replay_mode=trace.run_mode,
        step_failure_index=trace.step_failure_index,
        assertion_failures=assertion_failures,
        assertion_results=trace.assertion_results,
        trace_path=trace.trace_path,
        repair_confidence=avg_repair_confidence,
        stability_fingerprints=fingerprints,
        healing_decision=healing_decision,
        healed_steps=healed_steps,
        performance_metrics=trace.performance_metrics,
        intent_contract=flow.intent_contract,
        drift_summary=_build_drift_summary(
            flow=flow,
            status=status,
            replay_mode=trace.run_mode,
            assertion_results=trace.assertion_results,
            failure_reason_codes=failure_reason_codes,
            rerecorded=False,
            actual_landing_url=trace.landing_url,
        ),
    )


# ---------------------------------------------------------------------------
# Goal-replay fallback (original behaviour, kept for goal_fallback mode)
# ---------------------------------------------------------------------------


async def execute_flow(
    flow: RecordedFlow,
    app_url: str,
    run_id: str,
    case_id: str,
    storage_state: Optional[str],
    headless: bool = True,
    verbose: bool = False,
    max_steps: int = 50,
    run_mode: str = "hybrid",
    auto_rerecord: bool = False,
    profile_name: Optional[str] = None,
) -> FailureCase:
    """Replay a RecordedFlow.

    Uses hybrid step-by-step replay by default (run_mode='hybrid').
    Falls back to full goal-replay agent when run_mode='goal_fallback'.
    When auto_rerecord is True and the flow fails, attempts a hard-heal
    by re-recording the flow via the original goal and replacing the saved steps.
    """
    # Per-flow override takes precedence over the run-level run_mode
    effective_mode = getattr(flow, "run_mode_override", None) or run_mode
    if effective_mode != "goal_fallback" and flow.steps:
        result = await execute_recorded_flow(
            flow=flow,
            run_id=run_id,
            case_id=case_id,
            storage_state=storage_state,
            headless=False if verbose else headless,
            run_mode=effective_mode,
        )

        # Hard heal: if flow failed and auto_rerecord is enabled, re-record via goal
        if auto_rerecord and result.status in ("fail", "error") and effective_mode == "hybrid":
            try:
                rerecord_result = await _hard_heal_rerecord(
                    flow=flow,
                    app_url=app_url,
                    run_id=run_id,
                    case_id=case_id,
                    storage_state=storage_state,
                    headless=headless,
                    max_steps=max_steps,
                    profile_name=profile_name,
                )
                if rerecord_result is not None:
                    return rerecord_result
            except Exception:
                _log.debug("hard heal rerecord failed", exc_info=True)

        return result

    return await _goal_fallback(
        flow=flow,
        app_url=app_url,
        run_id=run_id,
        case_id=case_id,
        storage_state=storage_state,
        headless=headless,
        verbose=verbose,
        max_steps=max_steps,
    )


async def _hard_heal_rerecord(
    flow: RecordedFlow,
    app_url: str,
    run_id: str,
    case_id: str,
    storage_state: Optional[str],
    headless: bool,
    max_steps: int,
    profile_name: Optional[str] = None,
) -> FailureCase | None:
    """Re-record a flow from its original goal, replace stored steps, and return a new case."""
    try:
        from blop.tools.record import record_test_flow as _record_tool

        rerecord_result = await _record_tool(
            app_url=app_url,
            flow_name=flow.flow_name,
            goal=flow.goal,
            profile_name=profile_name,
            command=None,
            business_criticality=flow.business_criticality,
        )
        if rerecord_result.get("status") == "recorded":
            rerecorded_flow_id = rerecord_result.get("flow_id") or flow.flow_id
            case = FailureCase(
                case_id=case_id,
                run_id=run_id,
                flow_id=rerecorded_flow_id,
                flow_name=flow.flow_name,
                status="pass",
                severity="none",
                replay_mode="hard_heal_rerecord",
                rerecorded=True,
                raw_result=f"Flow re-recorded successfully as {rerecorded_flow_id}",
                intent_contract=flow.intent_contract,
                drift_summary=_build_drift_summary(
                    flow=flow,
                    status="pass",
                    replay_mode="hard_heal_rerecord",
                    assertion_results=[],
                    failure_reason_codes=[],
                    rerecorded=True,
                ),
            )
            return case
    except Exception:
        _log.debug("record tool for hard heal failed", exc_info=True)
    return None


async def _goal_fallback(
    flow: RecordedFlow,
    app_url: str,
    run_id: str,
    case_id: str,
    storage_state: Optional[str],
    headless: bool,
    verbose: bool,
    max_steps: int,
) -> FailureCase:
    """Original goal-based agent replay."""
    from browser_use import Agent, BrowserSession

    from blop.engine.browser import make_browser_profile
    from blop.engine.llm_factory import make_agent_llm, make_planning_llm

    run_headless = False if verbose else headless
    evidence_policy = resolve_evidence_policy()

    browser_profile = make_browser_profile(headless=run_headless, storage_state=storage_state)
    browser_session = BrowserSession(browser_profile=browser_profile)

    console_errors: list[str] = []
    network_errors: list[str] = []
    screenshots: list[str] = []
    raw_result = ""
    status = "error"
    final_url = ""

    try:
        llm = make_agent_llm(role="agent")
        page_extraction_llm = make_planning_llm(temperature=0.0, max_output_tokens=256, role="summary")
        from blop.engine.auth_prompt import append_runtime_auth_guidance

        task = append_runtime_auth_guidance(f"Navigate to {app_url} then: {flow.goal}")
        from blop.engine.recording import SPA_AGENT_RULES

        _is_heavy = getattr(flow, "spa_hints", None) and getattr(flow.spa_hints, "is_editor_heavy", False)
        _system_msg = SPA_AGENT_RULES
        if _is_heavy:
            _system_msg += (
                " IMPORTANT: This flow targets a canvas/WebGL-heavy application. "
                "Wait patiently — up to 45 seconds — for the canvas view to initialise before declaring failure. "
                "Only assert on DOM toolbar elements, never on canvas content."
            )
        agent = Agent(
            task=task,
            llm=llm,
            browser_session=browser_session,
            use_vision="auto",
            flash_mode=True,
            page_extraction_llm=page_extraction_llm,
            extend_system_message=_system_msg,
        )

        screenshot_task: Optional[asyncio.Task] = None
        step_idx = 0

        async def _poll_screenshots():
            nonlocal step_idx
            while True:
                try:
                    if not should_capture_screenshot(evidence_policy, "periodic"):
                        return
                    await asyncio.sleep(evidence_policy.screenshot_interval_secs)
                    if step_idx >= evidence_policy.max_screenshots:
                        return
                    shot_path = file_store.screenshot_path(run_id, case_id, step_idx)
                    await browser_session.take_screenshot(path=shot_path)
                    screenshots.append(shot_path)
                    step_idx += 1
                except asyncio.CancelledError:
                    break
                except Exception:
                    _log.debug("poll screenshot failed", exc_info=True)

        if should_capture_screenshot(evidence_policy, "periodic"):
            screenshot_task = asyncio.create_task(_poll_screenshots())

        goal_trace_path: Optional[str] = None
        try:
            history = await agent.run(max_steps=max_steps)

            # Guaranteed final screenshot after agent completes
            try:
                if should_capture_screenshot(evidence_policy, "final"):
                    final_path = file_store.screenshot_path(run_id, case_id, 999)
                    await browser_session.take_screenshot(path=final_path)
                    if final_path not in screenshots:
                        screenshots.append(final_path)
            except Exception:
                _log.debug("goal fallback final screenshot failed", exc_info=True)

            # Prefer done-action text over final_result() which only returns ExtractAction content
            # model_dump() includes ALL action type keys (most None) — find the non-None one
            raw_result = ""
            done_success = True
            done_found = False
            if hasattr(history, "model_actions"):
                try:
                    for action in reversed(history.model_actions()):
                        done_val = action.get("done")
                        if done_val is not None:
                            if isinstance(done_val, dict):
                                raw_result = str(done_val.get("text") or done_val)
                                done_success = bool(done_val.get("success", True))
                            else:
                                raw_result = str(done_val)
                            done_found = True
                            break
                except Exception:
                    _log.debug("model_actions iteration failed", exc_info=True)
            if not raw_result:
                raw_result = str(history.final_result()) if hasattr(history, "final_result") else str(history)

            # When no done action was found, fall back to keyword scanning of raw_result.
            # This catches cases where the agent silently ended without a done action.
            if not done_found:
                _kw_fail = any(
                    w in raw_result.lower()
                    for w in (
                        "error",
                        "fail",
                        "broken",
                        "exception",
                        "crash",
                        "404",
                        "500",
                        "unable",
                        "could not",
                        "cannot",
                    )
                )
                if _kw_fail:
                    done_success = False

            # Trust the agent's done_success boolean as the primary signal.
            # Additionally catch hard browser-level failures (404, error pages)
            # that the agent may not detect — e.g. logout redirecting to a 404.
            final_url = ""
            final_page_text = ""
            try:
                final_url = await browser_session.get_current_page_url() or ""
                page = await browser_session.get_current_page()
                if page:
                    raw_page_text = await page.inner_text("body")
                    final_page_text = (await _normalize_page_text(raw_page_text)).lower()[:500]
            except Exception:
                _log.debug("get final page state failed", exc_info=True)

            _hard_fail = (
                "404" in final_page_text
                or "page not found" in final_page_text
                or "did you forget to add the page" in final_page_text
                or "500" in final_page_text
                or "internal server error" in final_page_text
            )

            if not done_success or _hard_fail:
                if _hard_fail and done_success:
                    raw_result += f" [browser ended on error page: {final_url}]"
                status = "fail"
            else:
                status = "pass"
        finally:
            if screenshot_task:
                screenshot_task.cancel()
                try:
                    await screenshot_task
                except asyncio.CancelledError:
                    pass

        try:
            ctx = getattr(browser_session, "context", None)
            if ctx and ctx.pages and should_capture_screenshot(evidence_policy, "failure"):
                final_path = file_store.screenshot_path(run_id, case_id, step_idx)
                await ctx.pages[0].screenshot(path=final_path)
                screenshots.append(final_path)
        except Exception:
            _log.debug("goal fallback context screenshot failed", exc_info=True)

        if console_errors:
            log_path = file_store.console_log_path(run_id, case_id)
            with open(log_path, "w") as f:
                f.write("\n".join(console_errors))

    except Exception as e:
        raw_result = str(e)
        status = "error"
    finally:
        try:
            await browser_session.aclose()
        except Exception:
            _log.debug("browser session close failed", exc_info=True)

    return FailureCase(
        case_id=case_id,
        run_id=run_id,
        flow_id=flow.flow_id,
        flow_name=flow.flow_name,
        status=status,
        severity="none",
        repro_steps=[],
        console_errors=console_errors,
        network_errors=network_errors,
        screenshots=cap_artifact_paths(screenshots, limit=evidence_policy.artifact_cap),
        raw_result=raw_result,
        replay_mode="goal_fallback",
        trace_path=goal_trace_path,
        intent_contract=flow.intent_contract,
        drift_summary=_build_drift_summary(
            flow=flow,
            status=status,
            replay_mode="goal_fallback",
            assertion_results=[],
            failure_reason_codes=["auth_redirect"] if "/login" in (final_url or "").lower() else [],
            rerecorded=False,
            actual_landing_url=final_url or None,
        ),
    )


async def run_flows(
    flows: list[RecordedFlow],
    app_url: str,
    run_id: str,
    storage_state: Optional[str],
    headless: bool,
    max_steps: int = 50,
    run_mode: str = "hybrid",
    auto_rerecord: bool = False,
    profile_name: Optional[str] = None,
    execution_metadata: dict[str, dict] | None = None,
    on_case_completed: Callable[[FailureCase, RecordedFlow], Awaitable[None] | None] | None = None,
) -> list[FailureCase]:
    """Execute all flows in parallel with bounded worker slots and section-aware ordering."""
    import uuid as _uuid

    if not flows:
        return []

    # Playwright tracing with DOM snapshots is memory-intensive; reduce concurrency
    # whenever at least one flow will execute in step-replay mode.
    _tracing_active = any((getattr(flow, "run_mode_override", None) or run_mode) != "goal_fallback" for flow in flows)
    default_workers = 3 if _tracing_active else 5
    worker_count = max(1, min(BLOP_REPLAY_CONCURRENCY or default_workers, len(flows)))
    ordered_flows = _interleave_flows_by_entry_area(flows)
    results: list[FailureCase | Exception | None] = [None] * len(flows)
    original_positions = {flow.flow_id: idx for idx, flow in enumerate(flows)}
    queue: asyncio.Queue[tuple[int, RecordedFlow]] = asyncio.Queue()

    for flow in ordered_flows:
        await queue.put((original_positions[flow.flow_id], flow))

    async def run_one(worker_slot: int) -> None:
        while True:
            try:
                original_idx, flow = queue.get_nowait()
            except asyncio.QueueEmpty:
                return
            if execution_metadata is not None:
                execution_metadata[flow.flow_id] = {
                    "worker_slot": worker_slot,
                    "entry_area_key": _flow_entry_area_key(flow),
                }
            cid = _uuid.uuid4().hex
            try:
                result_case = await execute_flow(
                    flow=flow,
                    app_url=app_url,
                    run_id=run_id,
                    case_id=cid,
                    storage_state=storage_state,
                    headless=headless,
                    max_steps=max_steps,
                    run_mode=run_mode,
                    auto_rerecord=auto_rerecord,
                    profile_name=profile_name,
                )
                results[original_idx] = result_case
                if on_case_completed is not None:
                    try:
                        maybe = on_case_completed(result_case, flow)
                        if inspect.isawaitable(maybe):
                            await maybe
                    except Exception:
                        _log.debug("run_flows on_case_completed failed", exc_info=True)
            except Exception as exc:
                results[original_idx] = exc
            finally:
                queue.task_done()

    await asyncio.gather(*(run_one(worker_slot) for worker_slot in range(1, worker_count + 1)))

    cases: list[FailureCase] = []
    for i, result in enumerate(results):
        if isinstance(result, Exception):
            _log.debug(
                "run_flows orchestration_error run_id=%s flow_id=%s error=%s",
                run_id,
                flows[i].flow_id if i < len(flows) else "unknown",
                str(result),
                exc_info=True,
            )
            cases.append(
                FailureCase(
                    run_id=run_id,
                    flow_id=flows[i].flow_id if i < len(flows) else "unknown",
                    flow_name=flows[i].flow_name if i < len(flows) else "unknown",
                    status="error",
                    raw_result=str(result),
                    replay_mode="orchestration_error",
                    failure_reason_codes=["orchestration_error"],
                )
            )
        elif result is not None:
            cases.append(result)

    return cases


def _flow_entry_area_key(flow: RecordedFlow) -> str:
    candidate = getattr(flow, "entry_url", None) or ""
    if not candidate:
        for step in getattr(flow, "steps", []):
            if getattr(step, "action", None) == "navigate":
                candidate = getattr(step, "value", None) or getattr(step, "url_after", None) or ""
                if candidate:
                    break
    parsed = urlparse(candidate or getattr(flow, "app_url", "") or "")
    path = parsed.path or "/"
    segments = [segment for segment in path.split("/") if segment]
    return segments[0].lower() if segments else "/"


def _interleave_flows_by_entry_area(flows: list[RecordedFlow]) -> list[RecordedFlow]:
    area_order: list[str] = []
    buckets: dict[str, list[RecordedFlow]] = {}
    for flow in flows:
        area_key = _flow_entry_area_key(flow)
        if area_key not in buckets:
            buckets[area_key] = []
            area_order.append(area_key)
        buckets[area_key].append(flow)

    interleaved: list[RecordedFlow] = []
    while any(buckets.get(area_key) for area_key in area_order):
        for area_key in area_order:
            bucket = buckets.get(area_key) or []
            if bucket:
                interleaved.append(bucket.pop(0))
    return interleaved
