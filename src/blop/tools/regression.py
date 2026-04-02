from __future__ import annotations

import asyncio
import inspect
import os
import time
import traceback
import uuid
from typing import Annotated, Literal, Optional

from pydantic import Field

from blop.config import BLOP_MAX_CONCURRENT_RUNS
from blop.engine import auth as auth_engine
from blop.engine import classifier
from blop.engine import regression as regression_engine
from blop.engine.errors import BLOP_REGRESSION_START_FAILED, BlopError
from blop.engine.logger import get_logger
from blop.mcp.envelope import build_poll_workflow_hint
from blop.reporting.results import explain_run_status
from blop.schemas import RunStartedResult
from blop.storage import files as file_store
from blop.storage import sqlite

_log = get_logger("tools.regression")
_RUN_TASKS: dict[str, asyncio.Task] = {}
_PENDING_DB_FINALIZERS: set[asyncio.Task] = set()
_TERMINAL_RUN_STATUSES = {"completed", "failed", "cancelled", "interrupted", "waiting_auth"}
_RUN_CHECKPOINT_KEY = "durable_checkpoint"


def _durability_mode() -> str:
    from blop.config import BLOP_DURABILITY_MODE

    return BLOP_DURABILITY_MODE


def _build_checkpoint_payload(
    *,
    run_id: str,
    app_url: str,
    all_flow_ids: list[str],
    completed_cases: list,
    profile_name: str | None,
    run_mode: str,
    status: str,
    reason: str | None = None,
) -> dict:
    completed_flow_ids = [
        flow_id
        for flow_id in dict.fromkeys(
            getattr(case, "flow_id", None) for case in completed_cases if getattr(case, "flow_id", None)
        )
        if flow_id
    ]
    completed_case_ids = [
        case_id
        for case_id in dict.fromkeys(
            getattr(case, "case_id", None) for case in completed_cases if getattr(case, "case_id", None)
        )
        if case_id
    ]
    remaining_flow_ids = [flow_id for flow_id in all_flow_ids if flow_id not in completed_flow_ids]
    return {
        "run_id": run_id,
        "app_url": app_url,
        "profile_name": profile_name,
        "run_mode": run_mode,
        "status": status,
        "reason": reason,
        "flow_ids": list(all_flow_ids),
        "completed_flow_ids": completed_flow_ids,
        "completed_case_ids": completed_case_ids,
        "remaining_flow_ids": remaining_flow_ids,
        "total_flow_count": len(all_flow_ids),
        "completed_flow_count": len(completed_flow_ids),
        "remaining_flow_count": len(remaining_flow_ids),
        "last_case_id": completed_case_ids[-1] if completed_case_ids else None,
        "updated_at": time.time(),
    }


async def _persist_checkpoint(
    *,
    run_id: str,
    app_url: str,
    all_flow_ids: list[str],
    completed_cases: list,
    profile_name: str | None,
    run_mode: str,
    status: str,
    reason: str | None = None,
    flush: bool = False,
) -> dict:
    payload = _build_checkpoint_payload(
        run_id=run_id,
        app_url=app_url,
        all_flow_ids=all_flow_ids,
        completed_cases=completed_cases,
        profile_name=profile_name,
        run_mode=run_mode,
        status=status,
        reason=reason,
    )
    await sqlite.upsert_run_observation(run_id, _RUN_CHECKPOINT_KEY, payload)
    if flush or _durability_mode() == "sync":
        await sqlite.flush_buffered_writes(run_id=run_id)
    return payload


async def _refresh_linked_release_brief(run_id: str) -> None:
    try:
        from blop.tools.release_check import refresh_release_brief_after_run

        await refresh_release_brief_after_run(run_id)
    except Exception:
        _log.debug("refresh_linked_release_brief_failed run_id=%s", run_id, exc_info=True)


def _auth_redirect_detected(url: str) -> bool:
    lowered = (url or "").lower()
    return any(token in lowered for token in ("/login", "/signin", "/sign-in", "/auth", "oauth"))


def _regression_flow_mode(flows: list) -> tuple[str, str | None]:
    """Return ('web'|'mobile', None) or ('error', message)."""
    if not flows:
        return "error", "no flows"
    platforms = [getattr(f, "platform", "web") for f in flows]
    if all(p == "web" for p in platforms):
        return "web", None
    if all(p in ("ios", "android") for p in platforms):
        missing = [f.flow_id for f in flows if getattr(f, "mobile_target", None) is None]
        if missing:
            return (
                "error",
                "Mobile flows must have mobile_target set (re-record with record_test_flow "
                f"platform=ios|android). Missing for flow_ids: {missing}",
            )
        return "mobile", None
    return (
        "error",
        "Cannot mix web and mobile flows in one regression run. "
        "Use separate run_regression_test calls for web-only vs mobile-only flow_ids.",
    )


def _execution_plan_summary(flows: list, run_mode: str, profile_name: str | None) -> dict:
    contracts = [getattr(flow, "intent_contract", None) for flow in flows]
    target_surfaces = [contract.target_surface for contract in contracts if contract]
    planning_sources = [contract.planning_source for contract in contracts if contract]
    required_assertions = sum(len(contract.success_assertions) for contract in contracts if contract)
    return {
        "effective_run_mode": run_mode,
        "profile_name": profile_name,
        "target_surfaces": list(dict.fromkeys(target_surfaces)) or ["unknown"],
        "planning_sources": list(dict.fromkeys(planning_sources)) or ["legacy_unstructured"],
        "legacy_flow_count": sum(1 for contract in contracts if contract is None),
        "required_assertion_count": required_assertions,
    }


def _start_error(message: str) -> dict:
    err = BlopError(
        BLOP_REGRESSION_START_FAILED,
        message,
        details={"stage": "run_regression_test"},
    )
    return err.to_merged_response(
        run_id=None,
        status="error",
        message=message,
        flow_count=0,
        artifacts_dir="",
    )


def _concurrency_exceeded_response(active: int, limit: int) -> dict:
    err = BlopError(
        "BLOP_RUN_CONCURRENCY_EXCEEDED",
        f"Too many concurrent runs ({active}/{limit}). "
        "Wait for existing runs to complete or raise BLOP_MAX_CONCURRENT_RUNS.",
        retryable=True,
        details={"active": active, "limit": limit},
    )
    return err.to_merged_response(
        run_id=None,
        status="error",
        message=err.message,
        flow_count=0,
        artifacts_dir="",
    )


async def _return_waiting_auth(
    *,
    run_id: str,
    app_url: str,
    profile_name: str | None,
    flow_ids: list[str],
    flow_count: int,
    artifacts_dir: str,
    headless: bool,
    run_mode: str,
    auth_context_payload: dict,
    startup_timing_ms: dict[str, int],
    message: str,
) -> dict:
    queued_payload = {
        "app_url": app_url,
        "flow_count": flow_count,
        "run_mode": auth_context_payload.get("run_mode", "hybrid"),
        "profile_name": profile_name,
        "startup_timing_ms": startup_timing_ms,
    }
    auth_payload = {**auth_context_payload, "startup_timing_ms": startup_timing_ms}
    await sqlite.create_run_with_initial_events(
        run_id=run_id,
        app_url=app_url,
        profile_name=profile_name,
        flow_ids=flow_ids,
        headless=headless,
        artifacts_dir=artifacts_dir,
        run_mode=run_mode,
        status="waiting_auth",
        run_queued_payload=queued_payload,
        auth_context_payload=auth_payload,
    )
    status_meta = explain_run_status("waiting_auth", run_id=run_id)
    return {
        "run_id": run_id,
        "status": "waiting_auth",
        "flow_count": flow_count,
        "artifacts_dir": artifacts_dir,
        "message": message,
        "status_detail": status_meta["status_detail"],
        "recommended_next_action": status_meta["recommended_next_action"],
        "is_terminal": status_meta["is_terminal"],
        "workflow_hint": message,
    }


def get_run_task(run_id: str) -> Optional[asyncio.Task]:
    return _RUN_TASKS.get(run_id)


def _spawn_background_task(coro) -> asyncio.Task | asyncio.Future:
    """Create a background task and safely close orphaned coroutines in tests."""
    task = asyncio.create_task(coro)
    if isinstance(task, (asyncio.Task, asyncio.Future)):
        return task
    if inspect.iscoroutine(coro):
        coro.close()
    loop = asyncio.get_running_loop()
    placeholder = loop.create_future()
    placeholder.set_result(None)
    return placeholder


def _register_run_task(run_id: str, task: asyncio.Task | asyncio.Future) -> None:
    _RUN_TASKS[run_id] = task

    def _on_task_done(t) -> None:
        _RUN_TASKS.pop(run_id, None)
        if not t.cancelled() and t.exception() is not None:

            async def _safe_mark_failed():
                try:
                    await sqlite.update_run(run_id, "failed", [], None, [])
                except Exception as exc:
                    _log.error("task_done_db_failed run_id=%s error=%s", run_id, exc, exc_info=True)

            pending = asyncio.create_task(_safe_mark_failed())
            _PENDING_DB_FINALIZERS.add(pending)
            pending.add_done_callback(lambda _: _PENDING_DB_FINALIZERS.discard(pending))

    task.add_done_callback(_on_task_done)


def cancel_run_task(run_id: str) -> bool:
    task = _RUN_TASKS.get(run_id)
    if not task or task.done():
        return False
    task.cancel()
    return True


async def cancel_run_task_with_drain(run_id: str, timeout_secs: float = 30.0) -> bool:
    """Cancel a task and wait up to ``timeout_secs`` for it to actually stop.

    Playwright context/browser close can hang; this enforces a hard deadline so
    the concurrency slot is released even if the underlying process gets stuck.
    Returns True if a task was found and cancelled (regardless of drain outcome).
    """
    task = _RUN_TASKS.get(run_id)
    if not task or task.done():
        return False
    task.cancel()
    try:
        await asyncio.wait_for(asyncio.shield(task), timeout=timeout_secs)
    except (asyncio.TimeoutError, asyncio.CancelledError):
        pass
    return True


async def _ensure_terminal_run_status(run_id: str, fallback_status: str) -> None:
    run = await sqlite.get_run(run_id)
    if not run:
        return
    current_status = str(run.get("status") or "")
    if current_status in _TERMINAL_RUN_STATUSES:
        return
    await sqlite.update_run_status(run_id, fallback_status)
    await sqlite.save_run_health_event(
        run_id,
        "run_force_terminated",
        {"previous_status": current_status, "new_status": fallback_status},
    )


async def _mark_run_resumable(run_id: str, reason: str) -> None:
    run = await sqlite.get_run(run_id)
    if not run:
        return
    checkpoint = await sqlite.get_run_observation(run_id, _RUN_CHECKPOINT_KEY)
    all_flow_ids = list(run.get("flow_ids") or [])
    completed_cases = []
    if checkpoint:
        completed_case_ids = set(checkpoint.get("completed_case_ids") or [])
        existing_cases = await sqlite.list_cases_for_runs([run_id])
        completed_cases = [case for case in existing_cases if case.case_id in completed_case_ids]
    await sqlite.update_run_status(run_id, "queued")
    await _persist_checkpoint(
        run_id=run_id,
        app_url=run.get("app_url", ""),
        all_flow_ids=all_flow_ids,
        completed_cases=completed_cases,
        profile_name=run.get("profile_name"),
        run_mode=run.get("run_mode", "hybrid"),
        status="queued",
        reason=reason,
        flush=True,
    )
    await sqlite.save_run_health_event(
        run_id,
        "run_checkpointed",
        {
            "reason": reason,
            "resume_supported": True,
            "completed_flow_count": len({case.flow_id for case in completed_cases}),
            "remaining_flow_count": len(
                [flow_id for flow_id in all_flow_ids if flow_id not in {case.flow_id for case in completed_cases}]
            ),
        },
    )


async def shutdown_run_tasks(timeout_secs: float = 10.0) -> dict:
    """Cancel and drain active regression tasks during process shutdown."""
    active = [(run_id, task) for run_id, task in _RUN_TASKS.items() if not task.done()]
    if not active:
        return {"cancelled": 0, "timed_out": 0, "forced": 0}
    durability_mode = _durability_mode()

    for _, task in active:
        try:
            task.cancel()
        except Exception:
            _log.debug("shutdown task cancel failed", exc_info=True)

    waiting_tasks = [task for _, task in active]
    forced = 0
    try:
        done, pending = await asyncio.wait(waiting_tasks, timeout=max(timeout_secs, 0.1))
    except Exception:
        done, pending = set(), set(waiting_tasks)

    for run_id, task in active:
        try:
            if task in pending:
                if durability_mode == "exit":
                    await _ensure_terminal_run_status(run_id, "cancelled")
                else:
                    await _mark_run_resumable(run_id, "shutdown_timeout")
                forced += 1
                continue
            if task.cancelled():
                if durability_mode == "exit":
                    await _ensure_terminal_run_status(run_id, "cancelled")
                else:
                    await _mark_run_resumable(run_id, "shutdown_cancelled")
            elif task.exception() is not None:
                await _ensure_terminal_run_status(run_id, "failed")
        except Exception:
            _log.debug("shutdown task finalization failed", exc_info=True)
        finally:
            _RUN_TASKS.pop(run_id, None)

    if _PENDING_DB_FINALIZERS:
        pending_updates = list(_PENDING_DB_FINALIZERS)
        done_updates, _ = await asyncio.wait(pending_updates, timeout=3.0)
        for t in done_updates:
            _PENDING_DB_FINALIZERS.discard(t)

    return {"cancelled": len(done), "timed_out": len(pending), "forced": forced}


async def force_finalize_active_runs(reason: str = "process_shutdown") -> dict:
    """Best-effort terminalization for active runs when loop/task draining is unreliable."""
    active_run_ids = list(_RUN_TASKS.keys())
    forced = 0
    durability_mode = _durability_mode()
    for run_id in active_run_ids:
        task = _RUN_TASKS.get(run_id)
        if task and not task.done():
            try:
                task.cancel()
            except Exception:
                _log.debug("force finalize task cancel failed", exc_info=True)
        try:
            if durability_mode == "exit":
                await _ensure_terminal_run_status(run_id, "cancelled")
                await sqlite.save_run_health_event(
                    run_id,
                    "run_cancelled",
                    {"event": "run_cancelled", "reason": reason},
                )
            else:
                await _mark_run_resumable(run_id, reason)
            forced += 1
        except Exception:
            _log.debug("force finalize status update failed", exc_info=True)
        finally:
            _RUN_TASKS.pop(run_id, None)
    return {"forced_cancelled": forced}


async def resume_incomplete_runs(limit_per_status: int = 50) -> dict:
    """Resume queued/running runs with saved checkpoints when durability is enabled."""
    if _durability_mode() == "exit":
        return {"eligible": 0, "resumed": 0, "waiting_auth": 0, "skipped": 0}

    queued = await sqlite.list_runs(limit=limit_per_status, status="queued")
    running = await sqlite.list_runs(limit=limit_per_status, status="running")
    resumed = 0
    waiting_auth = 0
    skipped = 0
    eligible = 0
    seen: set[str] = set()

    for run in queued + running:
        run_id = str(run.get("run_id") or "")
        if not run_id or run_id in seen:
            continue
        seen.add(run_id)
        active_task = _RUN_TASKS.get(run_id)
        if active_task and not active_task.done():
            continue

        checkpoint = await sqlite.get_run_observation(run_id, _RUN_CHECKPOINT_KEY)
        if not checkpoint:
            continue
        remaining_flow_ids = list(checkpoint.get("remaining_flow_ids") or [])
        if not remaining_flow_ids:
            continue

        eligible += 1
        flows = await sqlite.get_flows(remaining_flow_ids)
        if not flows:
            skipped += 1
            continue
        flow_mode, flow_mode_err = _regression_flow_mode(flows)
        if flow_mode == "error":
            _log.warning("resume_incomplete_runs invalid_flow_set run_id=%s error=%s", run_id, flow_mode_err)
            skipped += 1
            continue

        mobile_only = flow_mode == "mobile"
        profile_name = run.get("profile_name")
        storage_state: Optional[str] = None
        if profile_name and not mobile_only:
            profile = await sqlite.get_auth_profile(profile_name)
            if profile is None:
                waiting_auth += 1
                await sqlite.update_run_status(run_id, "waiting_auth")
                await sqlite.save_run_health_event(
                    run_id,
                    "run_waiting_auth",
                    {"reason": "missing_profile", "profile_name": profile_name, "resumed_run": True},
                )
                continue
            try:
                storage_state = await auth_engine.resolve_storage_state(profile)
            except Exception:
                storage_state = None
            if not storage_state:
                waiting_auth += 1
                await sqlite.update_run_status(run_id, "waiting_auth")
                await sqlite.save_run_health_event(
                    run_id,
                    "run_waiting_auth",
                    {"reason": "unresolved_storage_state", "profile_name": profile_name, "resumed_run": True},
                )
                continue

        task = _spawn_background_task(
            _run_and_persist(
                run_id,
                flows,
                str(run.get("app_url") or ""),
                storage_state,
                bool(run.get("headless", True)),
                str(run.get("run_mode") or "hybrid"),
                False,
                profile_name,
                mobile_only=mobile_only,
            )
        )
        _register_run_task(run_id, task)
        await sqlite.save_run_health_event(
            run_id,
            "run_resumed",
            {
                "remaining_flow_count": len(flows),
                "completed_flow_count": len(checkpoint.get("completed_flow_ids") or []),
                "durability_mode": _durability_mode(),
            },
        )
        resumed += 1

    return {
        "eligible": eligible,
        "resumed": resumed,
        "waiting_auth": waiting_auth,
        "skipped": skipped,
    }


async def run_regression_test(
    app_url: str,
    flow_ids: list[str],
    profile_name: Annotated[
        Optional[str],
        Field(
            default=None,
            description=(
                "Name of a saved auth profile created with save_auth_profile. "
                "Required for apps with login. Example: 'staging', 'prod_admin'."
            ),
            examples=["staging", "prod_admin"],
        ),
    ] = None,
    headless: bool = True,
    run_mode: Annotated[
        Literal["replay", "hybrid", "strict_steps", "goal_fallback"],
        Field(
            default="hybrid",
            description=(
                "replay: replays previously recorded steps deterministically. "
                "hybrid: combines step replay with agent fallback on mismatch. "
                "strict_steps: replay only, no agent fallback. "
                "goal_fallback: agent-only re-execution from goal. "
                "Use replay for release checks; use hybrid or goal_fallback after major UI changes."
            ),
        ),
    ] = "hybrid",
    command: Optional[str] = None,
    auto_rerecord: bool = False,
) -> dict:
    from blop.config import check_llm_api_key, validate_app_url, validate_mobile_replay_app_url
    from blop.engine.planner import normalize_run_mode

    run_mode = normalize_run_mode(run_mode)

    active = sum(1 for t in _RUN_TASKS.values() if not t.done())
    if active >= BLOP_MAX_CONCURRENT_RUNS:
        return _concurrency_exceeded_response(active, BLOP_MAX_CONCURRENT_RUNS)

    if not flow_ids:
        return _start_error("flow_ids must include at least one recorded flow id")
    invalid_ids = [fid for fid in flow_ids if not isinstance(fid, str) or not fid.strip()]
    if invalid_ids:
        return _start_error("flow_ids must be a list of non-empty strings")

    launch_started = time.perf_counter()
    run_id = uuid.uuid4().hex
    flow_lookup_started = time.perf_counter()
    flows = await sqlite.get_flows(flow_ids)
    flow_lookup_ms = int((time.perf_counter() - flow_lookup_started) * 1000)
    found_flow_ids = {flow.flow_id for flow in flows}
    missing_flow_ids = [fid for fid in flow_ids if fid not in found_flow_ids]
    if not flows:
        return _start_error(
            "None of the provided flow_ids were found. Use list_recorded_tests to get valid flow_ids first."
        )
    if missing_flow_ids:
        return _start_error(
            f"Some flow_ids were not found: {missing_flow_ids}. "
            "Use list_recorded_tests and retry with only valid flow_ids."
        )

    flow_mode, flow_mode_err = _regression_flow_mode(flows)
    if flow_mode == "error":
        return _start_error(flow_mode_err or "invalid flow set")
    mobile_only = flow_mode == "mobile"

    if mobile_only:
        url_err = validate_mobile_replay_app_url(app_url)
    else:
        url_err = validate_app_url(app_url)
    if url_err:
        return _start_error(url_err)

    if not mobile_only:
        has_key, key_name = check_llm_api_key()
        if not has_key:
            return _start_error(
                f"{key_name} is not set. Add it to your .env file or environment. "
                "Without it, all test results will be invalid (assertions pass vacuously)."
            )

    # If command provided, parse for additional intent
    if command:
        from blop.engine.planner import parse_command

        intent = await parse_command(command, app_url, profile_name=profile_name)
        run_mode = normalize_run_mode(intent.run_mode)
        if intent.profile_name and not profile_name:
            profile_name = intent.profile_name

    profile = None
    if profile_name and not mobile_only:
        profile = await sqlite.get_auth_profile(profile_name)
        if profile is None:
            return _start_error(
                f"Auth profile '{profile_name}' was not found. "
                "Provide a valid profile_name, run save_auth_profile, or use capture_auth_session."
            )

    storage_state: Optional[str] = None
    artifacts_dir = file_store.artifacts_dir(run_id)
    auth_resolve_ms = 0
    if profile:
        auth_resolve_started = time.perf_counter()
        try:
            storage_state = await auth_engine.resolve_storage_state(profile)
        except Exception:
            auth_resolve_ms = int((time.perf_counter() - auth_resolve_started) * 1000)
            startup_timing_ms = {
                "flow_lookup": flow_lookup_ms,
                "auth_resolve": auth_resolve_ms,
                "auth_validate": 0,
                "db_persist": 0,
                "total_launch": int((time.perf_counter() - launch_started) * 1000),
            }
            auth_context_payload = {
                "profile_name": profile_name,
                "auth_used": True,
                "auth_source": getattr(profile, "auth_type", None),
                "storage_state_path": None,
                "user_data_dir": getattr(profile, "user_data_dir", None),
                "session_validation_status": "validation_error",
                "session_validation_error": "auth resolution failed",
                "run_mode": run_mode,
            }
            return await _return_waiting_auth(
                run_id=run_id,
                app_url=app_url,
                profile_name=profile_name,
                flow_ids=flow_ids,
                flow_count=len(flows),
                artifacts_dir=artifacts_dir,
                headless=headless,
                run_mode=run_mode,
                auth_context_payload=auth_context_payload,
                startup_timing_ms=startup_timing_ms,
                message=f"Auth profile '{profile_name}' could not be resolved. Refresh the session or credentials, then retry.",
            )
        auth_resolve_ms = int((time.perf_counter() - auth_resolve_started) * 1000)
        if not storage_state:
            startup_timing_ms = {
                "flow_lookup": flow_lookup_ms,
                "auth_resolve": auth_resolve_ms,
                "auth_validate": 0,
                "db_persist": 0,
                "total_launch": int((time.perf_counter() - launch_started) * 1000),
            }
            auth_context_payload = {
                "profile_name": profile_name,
                "auth_used": True,
                "auth_source": getattr(profile, "auth_type", None),
                "storage_state_path": None,
                "user_data_dir": getattr(profile, "user_data_dir", None),
                "session_validation_status": "unresolved_storage_state",
                "run_mode": run_mode,
            }
            return await _return_waiting_auth(
                run_id=run_id,
                app_url=app_url,
                profile_name=profile_name,
                flow_ids=flow_ids,
                flow_count=len(flows),
                artifacts_dir=artifacts_dir,
                headless=headless,
                run_mode=run_mode,
                auth_context_payload=auth_context_payload,
                startup_timing_ms=startup_timing_ms,
                message=f"Auth profile '{profile_name}' did not resolve a usable session. Refresh auth before replaying.",
            )

    auth_context_payload = {
        "profile_name": profile_name,
        "auth_used": bool(profile_name) and not mobile_only,
        "auth_source": getattr(profile, "auth_type", None) if profile else None,
        "storage_state_path": storage_state,
        "user_data_dir": getattr(profile, "user_data_dir", None) if profile else None,
        "session_validation_status": "not_requested",
        "mobile_only": mobile_only,
    }
    if mobile_only and profile_name:
        _log.info(
            "run_regression_test: ignoring profile_name=%s for mobile-only replay",
            profile_name,
        )
        auth_context_payload["session_validation_status"] = "not_applicable_mobile"
    auth_validate_ms = 0
    if storage_state:
        auth_validate_started = time.perf_counter()
        try:
            session_valid = await auth_engine.validate_auth_session(storage_state, app_url)
            auth_context_payload["session_validation_status"] = "valid" if session_valid else "expired_session"
        except Exception as exc:
            auth_context_payload["session_validation_status"] = "validation_error"
            auth_context_payload["session_validation_error"] = str(exc)[:200]
        auth_validate_ms = int((time.perf_counter() - auth_validate_started) * 1000)

    waiting_auth = profile and auth_context_payload["session_validation_status"] in {
        "expired_session",
        "validation_error",
    }
    startup_timing_ms = {
        "flow_lookup": flow_lookup_ms,
        "auth_resolve": auth_resolve_ms,
        "auth_validate": auth_validate_ms,
        "db_persist": 0,
        "total_launch": 0,
    }
    queued_payload = {
        "app_url": app_url,
        "flow_count": len(flows),
        "run_mode": run_mode,
        "profile_name": profile_name,
        "startup_timing_ms": startup_timing_ms,
        "flow_execution_mode": "mobile" if mobile_only else "web",
    }
    auth_context_payload = {**auth_context_payload, "startup_timing_ms": startup_timing_ms}

    db_persist_started = time.perf_counter()
    await sqlite.create_run_with_initial_events(
        run_id=run_id,
        app_url=app_url,
        profile_name=profile_name,
        flow_ids=flow_ids,
        headless=headless,
        artifacts_dir=artifacts_dir,
        run_mode=run_mode,
        status="waiting_auth" if waiting_auth else "queued",
        run_queued_payload=queued_payload,
        auth_context_payload=auth_context_payload,
    )
    if _durability_mode() != "exit":
        await _persist_checkpoint(
            run_id=run_id,
            app_url=app_url,
            all_flow_ids=flow_ids,
            completed_cases=[],
            profile_name=profile_name,
            run_mode=run_mode,
            status="waiting_auth" if waiting_auth else "queued",
            reason="created",
            flush=True,
        )
    startup_timing_ms["db_persist"] = int((time.perf_counter() - db_persist_started) * 1000)
    startup_timing_ms["total_launch"] = int((time.perf_counter() - launch_started) * 1000)
    final_startup_timing_ms = dict(startup_timing_ms)
    await sqlite.save_run_health_event(
        run_id,
        "run_startup_timing",
        final_startup_timing_ms,
    )

    _log.info(
        "run_startup_timing run_id=%s flow_lookup_ms=%s auth_resolve_ms=%s auth_validate_ms=%s db_persist_ms=%s total_launch_ms=%s",
        run_id,
        final_startup_timing_ms["flow_lookup"],
        final_startup_timing_ms["auth_resolve"],
        final_startup_timing_ms["auth_validate"],
        final_startup_timing_ms["db_persist"],
        final_startup_timing_ms["total_launch"],
        extra={
            "event": "run_startup_timing",
            "run_id": run_id,
            "flow_lookup_ms": final_startup_timing_ms["flow_lookup"],
            "auth_resolve_ms": final_startup_timing_ms["auth_resolve"],
            "auth_validate_ms": final_startup_timing_ms["auth_validate"],
            "db_persist_ms": final_startup_timing_ms["db_persist"],
            "total_launch_ms": final_startup_timing_ms["total_launch"],
            "profile_name": profile_name,
            "run_mode": run_mode,
            "flow_count": len(flows),
        },
    )

    if waiting_auth:
        status_meta = explain_run_status("waiting_auth", run_id=run_id)
        if auth_context_payload["session_validation_status"] == "expired_session":
            message = (
                f"Auth profile '{profile_name}' has an expired session for {app_url}. Refresh auth before replaying."
            )
        else:
            message = (
                f"Auth profile '{profile_name}' could not be validated against {app_url}. Re-check auth and retry."
            )
        return {
            "run_id": run_id,
            "status": "waiting_auth",
            "flow_count": len(flows),
            "artifacts_dir": artifacts_dir,
            "message": message,
            "status_detail": status_meta["status_detail"],
            "recommended_next_action": status_meta["recommended_next_action"],
            "is_terminal": status_meta["is_terminal"],
            "workflow_hint": message,
        }

    # Fire-and-forget; caller polls get_test_results
    task = _spawn_background_task(
        _run_and_persist(
            run_id,
            flows,
            app_url,
            storage_state,
            headless,
            run_mode,
            auto_rerecord,
            profile_name,
            mobile_only=mobile_only,
        )
    )
    _log.info(
        "run_transition run_id=%s from_status=%s to_status=%s flow_count=%s",
        run_id,
        "new",
        "queued",
        len(flows),
        extra={
            "event": "run_transition",
            "run_id": run_id,
            "from_status": "new",
            "to_status": "queued",
            "flow_count": len(flows),
            "run_mode": run_mode,
            "profile_name": profile_name,
        },
    )
    _register_run_task(run_id, task)

    started = RunStartedResult(
        run_id=run_id,
        status="queued",
        flow_count=len(flows),
        artifacts_dir=artifacts_dir,
        replay_worker_count=(1 if mobile_only else regression_engine.compute_replay_worker_count(flows, run_mode)),
        flow_ids=[f.flow_id for f in flows],
    ).model_dump()
    status_meta = explain_run_status("queued", run_id=run_id)
    started["execution_plan_summary"] = _execution_plan_summary(flows, run_mode, profile_name)
    started["status_detail"] = status_meta["status_detail"]
    started["recommended_next_action"] = status_meta["recommended_next_action"]
    started["is_terminal"] = status_meta["is_terminal"]
    started["workflow_hint"] = status_meta["recommended_next_action"]
    started["workflow"] = build_poll_workflow_hint(run_id=run_id, flow_count=len(flows)).model_dump()
    return started


async def _run_and_persist(
    run_id: str,
    flows: list,
    app_url: str,
    storage_state: Optional[str],
    headless: bool,
    run_mode: str = "hybrid",
    auto_rerecord: bool = False,
    profile_name: Optional[str] = None,
    *,
    mobile_only: bool = False,
) -> None:
    from datetime import datetime, timezone

    durability_mode = _durability_mode()
    all_flow_ids = [flow.flow_id for flow in flows]
    flow_lookup = {flow.flow_id: flow for flow in flows}
    existing_cases = await sqlite.list_cases_for_runs([run_id]) if durability_mode != "exit" else []
    classified_by_flow = {case.flow_id: case for case in existing_cases if getattr(case, "flow_id", None)}
    remaining_flows = [flow for flow in flows if flow.flow_id not in classified_by_flow]
    artifacts_dir = file_store.artifacts_dir(run_id)

    # Transition: queued → running
    await sqlite.update_run_status(run_id, "running")
    if durability_mode != "exit":
        await _persist_checkpoint(
            run_id=run_id,
            app_url=app_url,
            all_flow_ids=all_flow_ids,
            completed_cases=list(classified_by_flow.values()),
            profile_name=profile_name,
            run_mode=run_mode,
            status="running",
            reason="started",
            flush=True,
        )
    _log.info(
        "run_transition run_id=%s from_status=%s to_status=%s",
        run_id,
        "queued",
        "running",
        extra={
            "event": "run_transition",
            "run_id": run_id,
            "from_status": "queued",
            "to_status": "running",
            "flow_count": len(flows),
            "run_mode": run_mode,
            "profile_name": profile_name,
        },
    )
    await sqlite.save_run_health_event(
        run_id,
        "run_started",
        {
            "flow_count": len(all_flow_ids),
            "remaining_flow_count": len(remaining_flows),
            "resumed_flow_count": len(classified_by_flow),
            "run_mode": run_mode,
            "headless": headless,
            "mobile_only": mobile_only,
            "replay_worker_count": 1
            if mobile_only
            else regression_engine.compute_replay_worker_count(remaining_flows, run_mode),
            "isolated_browser_context_per_flow": True,
        },
    )

    execution_metadata: dict[str, dict] = {}
    checkpoint_lock = asyncio.Lock()

    async def _persist_case_progress(case, _flow=None) -> None:
        case.business_criticality = getattr(flow_lookup.get(case.flow_id), "business_criticality", "other")
        classified_case = await classifier.classify_case(case, app_url)
        case_meta = execution_metadata.get(case.flow_id, {})
        event = {
            "run_id": run_id,
            "event_type": "case_completed",
            "payload": {
                "case_id": classified_case.case_id,
                "flow_id": classified_case.flow_id,
                "flow_name": classified_case.flow_name,
                "status": classified_case.status,
                "severity": classified_case.severity,
                "replay_mode": classified_case.replay_mode,
                "step_failure_index": classified_case.step_failure_index,
                "business_criticality": classified_case.business_criticality,
                "repair_confidence": classified_case.repair_confidence,
                "healing_decision": classified_case.healing_decision,
                "worker_slot": case_meta.get("worker_slot"),
                "entry_area_key": case_meta.get("entry_area_key"),
            },
        }
        async with checkpoint_lock:
            classified_by_flow[classified_case.flow_id] = classified_case
            await sqlite.save_cases([classified_case])
            await sqlite.save_run_health_events([event])
            if durability_mode != "exit":
                await _persist_checkpoint(
                    run_id=run_id,
                    app_url=app_url,
                    all_flow_ids=all_flow_ids,
                    completed_cases=list(classified_by_flow.values()),
                    profile_name=profile_name,
                    run_mode=run_mode,
                    status="running",
                    reason="case_completed",
                    flush=False,
                )

    try:
        from blop.config import BLOP_RUN_TIMEOUT_SECS
        from blop.engine.pipeline import RunContext, build_default_pipeline, persist_bus_events

        pipe_ctx = RunContext(
            run_id=run_id,
            app_url=app_url,
            flow_ids=all_flow_ids,
            profile_name=profile_name,
        )
        pipe_ctx.flows = list(remaining_flows)
        pipe_ctx.headless = headless
        pipe_ctx.run_mode = run_mode
        pipe_ctx.auto_rerecord = auto_rerecord
        pipe_ctx.mobile_only = mobile_only
        pipe_ctx.auth_state = storage_state
        pipe_ctx.skip_auth_resolution = True
        pipe_ctx.incremental_classify = not mobile_only
        pipe_ctx.execution_metadata = execution_metadata
        pipe_ctx.on_case_completed = None if mobile_only else _persist_case_progress
        pipe_ctx.artifacts_dir = str(artifacts_dir)

        async def _run_pipeline() -> None:
            pipeline = build_default_pipeline()
            try:
                await pipeline.run(pipe_ctx)
            finally:
                await persist_bus_events(pipe_ctx.bus)

        if BLOP_RUN_TIMEOUT_SECS > 0:
            await asyncio.wait_for(_run_pipeline(), timeout=float(BLOP_RUN_TIMEOUT_SECS))
        else:
            await _run_pipeline()

        for case in pipe_ctx.classified_cases:
            classified_by_flow[case.flow_id] = case
        classified = [classified_by_flow[flow_id] for flow_id in all_flow_ids if flow_id in classified_by_flow]

        next_actions = (pipe_ctx.run_summary or {}).get("next_actions", [])

        completed_at = datetime.now(timezone.utc).isoformat()
        await sqlite.update_run(run_id, "completed", classified, completed_at, next_actions)
        if durability_mode != "exit":
            await _persist_checkpoint(
                run_id=run_id,
                app_url=app_url,
                all_flow_ids=all_flow_ids,
                completed_cases=classified,
                profile_name=profile_name,
                run_mode=run_mode,
                status="completed",
                reason="completed",
                flush=True,
            )
        _log.info(
            "run_transition run_id=%s from_status=%s to_status=%s",
            run_id,
            "running",
            "completed",
            extra={
                "event": "run_transition",
                "run_id": run_id,
                "from_status": "running",
                "to_status": "completed",
                "case_count": len(classified),
                "next_action_count": len(next_actions),
            },
        )
        await sqlite.save_run_health_event(
            run_id,
            "run_completed",
            {
                "completed_at": completed_at,
                "passed": sum(1 for c in classified if c.status == "pass"),
                "failed": sum(1 for c in classified if c.status in ("fail", "error", "blocked")),
            },
        )
        await _refresh_linked_release_brief(run_id)

        # Record the release recommendation prediction for calibration tracking
        try:
            from blop.reporting.results import _compute_release_recommendation

            rec = _compute_release_recommendation(classified, "completed")
            await sqlite.save_risk_calibration_record(
                run_id=run_id,
                app_url=app_url,
                predicted_decision=rec["decision"],
                blocker_count=rec["blocker_count"],
                critical_journey_failures=rec["critical_journey_failures"],
                flow_ids=all_flow_ids,
            )
        except Exception:
            pass  # Calibration recording is best-effort; never block run completion
    except asyncio.TimeoutError:
        await sqlite.update_run(
            run_id=run_id,
            status="failed",
            cases=list(classified_by_flow.values()),
            completed_at=None,
            next_actions=[],
        )
        if durability_mode != "exit":
            await _persist_checkpoint(
                run_id=run_id,
                app_url=app_url,
                all_flow_ids=all_flow_ids,
                completed_cases=list(classified_by_flow.values()),
                profile_name=profile_name,
                run_mode=run_mode,
                status="failed",
                reason="run_timeout",
                flush=True,
            )
        _log.warning(
            "run_transition run_id=%s from_status=%s to_status=%s reason=%s",
            run_id,
            "running",
            "failed",
            "run_timeout",
            extra={
                "event": "run_transition",
                "run_id": run_id,
                "from_status": "running",
                "to_status": "failed",
                "reason": "run_timeout",
            },
        )
        await sqlite.save_run_health_event(
            run_id,
            "run_failed",
            {
                "event": "run_failed",
                "reason": "run_timeout",
                "error_type": "TimeoutError",
                "error_message": "Run exceeded BLOP_RUN_TIMEOUT_SECS",
            },
        )
        await _refresh_linked_release_brief(run_id)
    except asyncio.CancelledError:
        if durability_mode == "exit":
            await sqlite.update_run(
                run_id=run_id,
                status="cancelled",
                cases=list(classified_by_flow.values()),
                completed_at=None,
                next_actions=[],
            )
            _log.info(
                "run_transition run_id=%s from_status=%s to_status=%s reason=%s",
                run_id,
                "running",
                "cancelled",
                "task_cancelled",
                extra={
                    "event": "run_transition",
                    "run_id": run_id,
                    "from_status": "running",
                    "to_status": "cancelled",
                    "reason": "task_cancelled",
                },
            )
            await sqlite.save_run_health_event(
                run_id,
                "run_cancelled",
                {"event": "run_cancelled", "reason": "task_cancelled"},
            )
        else:
            await sqlite.update_run_status(run_id, "queued")
            await _persist_checkpoint(
                run_id=run_id,
                app_url=app_url,
                all_flow_ids=all_flow_ids,
                completed_cases=list(classified_by_flow.values()),
                profile_name=profile_name,
                run_mode=run_mode,
                status="queued",
                reason="task_cancelled",
                flush=True,
            )
            await sqlite.save_run_health_event(
                run_id,
                "run_checkpointed",
                {
                    "reason": "task_cancelled",
                    "resume_supported": True,
                    "completed_flow_count": len(classified_by_flow),
                    "remaining_flow_count": max(0, len(all_flow_ids) - len(classified_by_flow)),
                },
            )
        await _refresh_linked_release_brief(run_id)
        raise
    except Exception as e:
        await sqlite.update_run(
            run_id=run_id,
            status="failed",
            cases=list(classified_by_flow.values()),
            completed_at=None,
            next_actions=[],
        )
        if durability_mode != "exit":
            await _persist_checkpoint(
                run_id=run_id,
                app_url=app_url,
                all_flow_ids=all_flow_ids,
                completed_cases=list(classified_by_flow.values()),
                profile_name=profile_name,
                run_mode=run_mode,
                status="failed",
                reason="unhandled_exception",
                flush=True,
            )
        _log.warning(
            "run_transition run_id=%s from_status=%s to_status=%s reason=%s",
            run_id,
            "running",
            "failed",
            "unhandled_exception",
            extra={
                "event": "run_transition",
                "run_id": run_id,
                "from_status": "running",
                "to_status": "failed",
                "reason": "unhandled_exception",
                "error_type": type(e).__name__,
            },
        )
        event_payload = {
            "event": "run_failed",
            "reason": "unhandled_exception",
            "error_type": type(e).__name__,
            "error_message": str(e)[:500],
        }
        if os.getenv("BLOP_DEBUG", "0").lower() not in ("0", "false", "no", "off"):
            event_payload["traceback"] = traceback.format_exc(limit=20)[:4000]
        _log.debug("run_failed event=%s run_id=%s", event_payload, run_id)
        await sqlite.save_run_health_event(
            run_id,
            "run_failed",
            event_payload,
        )
        await _refresh_linked_release_brief(run_id)
