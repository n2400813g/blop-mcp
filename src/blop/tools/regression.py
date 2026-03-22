from __future__ import annotations

import asyncio
import inspect
import os
import traceback
import uuid
from typing import Optional

from blop.engine import auth as auth_engine
from blop.engine import classifier, regression as regression_engine
from blop.engine.logger import get_logger
from blop.reporting.results import explain_run_status
from blop.schemas import RunStartedResult
from blop.storage import sqlite, files as file_store

_log = get_logger("tools.regression")
_RUN_TASKS: dict[str, asyncio.Task] = {}
_PENDING_DB_FINALIZERS: set[asyncio.Task] = set()
_TERMINAL_RUN_STATUSES = {"completed", "failed", "cancelled", "waiting_auth"}


def _auth_redirect_detected(url: str) -> bool:
    lowered = (url or "").lower()
    return any(token in lowered for token in ("/login", "/signin", "/sign-in", "/auth", "oauth"))


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
    return {
        "error": message,
        "run_id": None,
        "status": "error",
        "message": message,
        "flow_count": 0,
        "artifacts_dir": "",
    }


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
    message: str,
) -> dict:
    await sqlite.create_run(run_id, app_url, profile_name, flow_ids, headless, artifacts_dir, run_mode)
    await sqlite.update_run_status(run_id, "waiting_auth")
    await sqlite.save_run_health_event(
        run_id,
        "run_queued",
        {
            "app_url": app_url,
            "flow_count": flow_count,
            "run_mode": auth_context_payload.get("run_mode", "hybrid"),
            "profile_name": profile_name,
        },
    )
    await sqlite.save_run_health_event(run_id, "auth_context_resolved", auth_context_payload)
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


def cancel_run_task(run_id: str) -> bool:
    task = _RUN_TASKS.get(run_id)
    if not task or task.done():
        return False
    task.cancel()
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


async def shutdown_run_tasks(timeout_secs: float = 10.0) -> dict:
    """Cancel and drain active regression tasks during process shutdown."""
    active = [(run_id, task) for run_id, task in _RUN_TASKS.items() if not task.done()]
    if not active:
        return {"cancelled": 0, "timed_out": 0, "forced": 0}

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
                await _ensure_terminal_run_status(run_id, "cancelled")
                forced += 1
                continue
            if task.cancelled():
                await _ensure_terminal_run_status(run_id, "cancelled")
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
    for run_id in active_run_ids:
        task = _RUN_TASKS.get(run_id)
        if task and not task.done():
            try:
                task.cancel()
            except Exception:
                _log.debug("force finalize task cancel failed", exc_info=True)
        try:
            await _ensure_terminal_run_status(run_id, "cancelled")
            await sqlite.save_run_health_event(
                run_id,
                "run_cancelled",
                {"event": "run_cancelled", "reason": reason},
            )
            forced += 1
        except Exception:
            _log.debug("force finalize status update failed", exc_info=True)
        finally:
            _RUN_TASKS.pop(run_id, None)
    return {"forced_cancelled": forced}


async def run_regression_test(
    app_url: str,
    flow_ids: list[str],
    profile_name: Optional[str] = None,
    headless: bool = True,
    run_mode: str = "hybrid",
    command: Optional[str] = None,
    auto_rerecord: bool = False,
) -> dict:
    from blop.engine.planner import normalize_run_mode
    from blop.config import check_llm_api_key, validate_app_url
    run_mode = normalize_run_mode(run_mode)

    url_err = validate_app_url(app_url)
    if url_err:
        return _start_error(url_err)

    _MAX_CONCURRENT = int(os.getenv("BLOP_MAX_CONCURRENT_RUNS", "10"))
    active = sum(1 for t in _RUN_TASKS.values() if not t.done())
    if active >= _MAX_CONCURRENT:
        return _start_error(
            f"Too many concurrent runs ({active}/{_MAX_CONCURRENT}). "
            "Wait for existing runs to complete or increase BLOP_MAX_CONCURRENT_RUNS."
        )

    if not flow_ids:
        return _start_error("flow_ids must include at least one recorded flow id")
    invalid_ids = [fid for fid in flow_ids if not isinstance(fid, str) or not fid.strip()]
    if invalid_ids:
        return _start_error("flow_ids must be a list of non-empty strings")
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

    run_id = uuid.uuid4().hex
    flows: list = []
    missing_flow_ids: list[str] = []
    for fid in flow_ids:
        flow = await sqlite.get_flow(fid)
        if flow:
            flows.append(flow)
        else:
            missing_flow_ids.append(fid)
    if not flows:
        return _start_error(
            "None of the provided flow_ids were found. "
            "Use list_recorded_tests to get valid flow_ids first."
        )
    if missing_flow_ids:
        return _start_error(
            f"Some flow_ids were not found: {missing_flow_ids}. "
            "Use list_recorded_tests and retry with only valid flow_ids."
        )

    profile = None
    if profile_name:
        profile = await sqlite.get_auth_profile(profile_name)
        if profile is None:
            return _start_error(
                f"Auth profile '{profile_name}' was not found. "
                "Provide a valid profile_name, run save_auth_profile, or use capture_auth_session."
            )

    storage_state: Optional[str] = None
    artifacts_dir = file_store.artifacts_dir(run_id)
    if profile:
        try:
            storage_state = await auth_engine.resolve_storage_state(profile)
        except Exception:
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
                message=f"Auth profile '{profile_name}' could not be resolved. Refresh the session or credentials, then retry.",
            )
        if not storage_state:
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
                message=f"Auth profile '{profile_name}' did not resolve a usable session. Refresh auth before replaying.",
            )

    await sqlite.create_run(run_id, app_url, profile_name, flow_ids, headless, artifacts_dir, run_mode)
    auth_context_payload = {
        "profile_name": profile_name,
        "auth_used": bool(profile_name),
        "auth_source": getattr(profile, "auth_type", None) if profile else None,
        "storage_state_path": storage_state,
        "user_data_dir": getattr(profile, "user_data_dir", None) if profile else None,
        "session_validation_status": "not_requested",
    }
    if storage_state:
        try:
            session_valid = await auth_engine.validate_auth_session(storage_state, app_url)
            auth_context_payload["session_validation_status"] = "valid" if session_valid else "expired_session"
        except Exception as exc:
            auth_context_payload["session_validation_status"] = "validation_error"
            auth_context_payload["session_validation_error"] = str(exc)[:200]
    if profile and auth_context_payload["session_validation_status"] in {"expired_session", "validation_error"}:
        await sqlite.update_run_status(run_id, "waiting_auth")
        await sqlite.save_run_health_event(
            run_id,
            "run_queued",
            {
                "app_url": app_url,
                "flow_count": len(flows),
                "run_mode": run_mode,
                "profile_name": profile_name,
            },
        )
        await sqlite.save_run_health_event(run_id, "auth_context_resolved", auth_context_payload)
        status_meta = explain_run_status("waiting_auth", run_id=run_id)
        if auth_context_payload["session_validation_status"] == "expired_session":
            message = f"Auth profile '{profile_name}' has an expired session for {app_url}. Refresh auth before replaying."
        else:
            message = f"Auth profile '{profile_name}' could not be validated against {app_url}. Re-check auth and retry."
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
    await sqlite.save_run_health_event(
        run_id,
        "run_queued",
        {
            "app_url": app_url,
            "flow_count": len(flows),
            "run_mode": run_mode,
            "profile_name": profile_name,
        },
    )
    await sqlite.save_run_health_event(run_id, "auth_context_resolved", auth_context_payload)

    # Fire-and-forget; caller polls get_test_results
    task = _spawn_background_task(
        _run_and_persist(run_id, flows, app_url, storage_state, headless, run_mode, auto_rerecord, profile_name)
    )
    _log.info(
        "run_transition run_id=%s from_status=%s to_status=%s flow_count=%s",
        run_id,
        "new",
        "queued",
        len(flows),
        extra={"run_id": run_id, "from_status": "new", "to_status": "queued", "flow_count": len(flows)},
    )
    _RUN_TASKS[run_id] = task

    def _on_task_done(t: asyncio.Task) -> None:
        _RUN_TASKS.pop(run_id, None)
        # If the task died before its own try/except could update the DB, mark it failed
        if not t.cancelled() and t.exception() is not None:
            async def _safe_mark_failed():
                try:
                    await sqlite.update_run(run_id, "failed", [], None, [])
                except Exception as exc:
                    _log.error(
                        "task_done_db_failed run_id=%s error=%s", run_id, exc, exc_info=True
                    )
            pending = asyncio.create_task(_safe_mark_failed())
            _PENDING_DB_FINALIZERS.add(pending)
            pending.add_done_callback(lambda _: _PENDING_DB_FINALIZERS.discard(pending))

    task.add_done_callback(_on_task_done)

    started = RunStartedResult(
        run_id=run_id,
        status="queued",
        flow_count=len(flows),
        artifacts_dir=artifacts_dir,
    ).model_dump()
    status_meta = explain_run_status("queued", run_id=run_id)
    started["execution_plan_summary"] = _execution_plan_summary(flows, run_mode, profile_name)
    started["status_detail"] = status_meta["status_detail"]
    started["recommended_next_action"] = status_meta["recommended_next_action"]
    started["is_terminal"] = status_meta["is_terminal"]
    started["workflow_hint"] = status_meta["recommended_next_action"]
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
) -> None:
    from datetime import datetime, timezone

    # Transition: queued → running
    await sqlite.update_run_status(run_id, "running")
    _log.info(
        "run_transition run_id=%s from_status=%s to_status=%s",
        run_id,
        "queued",
        "running",
        extra={"run_id": run_id, "from_status": "queued", "to_status": "running"},
    )
    await sqlite.save_run_health_event(
        run_id,
        "run_started",
        {
            "flow_count": len(flows),
            "run_mode": run_mode,
            "headless": headless,
        },
    )

    try:
        from blop.config import BLOP_RUN_TIMEOUT_SECS

        run_coro = regression_engine.run_flows(
            flows=flows,
            app_url=app_url,
            run_id=run_id,
            storage_state=storage_state,
            headless=headless,
            run_mode=run_mode,
            auto_rerecord=auto_rerecord,
            profile_name=profile_name,
        )
        if BLOP_RUN_TIMEOUT_SECS > 0:
            cases = await asyncio.wait_for(run_coro, timeout=float(BLOP_RUN_TIMEOUT_SECS))
        else:
            cases = await run_coro

        # Attach business_criticality from source flow to each case, then classify
        classified = []
        flow_criticality = {f.flow_id: f.business_criticality for f in flows}
        for case in cases:
            case.business_criticality = flow_criticality.get(case.flow_id, "other")
            classified.append(await classifier.classify_case(case, app_url))
            await sqlite.save_case(case)
            await sqlite.save_run_health_event(
                run_id,
                "case_completed",
                {
                    "case_id": case.case_id,
                    "flow_id": case.flow_id,
                    "flow_name": case.flow_name,
                    "status": case.status,
                    "severity": case.severity,
                    "replay_mode": case.replay_mode,
                    "step_failure_index": case.step_failure_index,
                    "business_criticality": case.business_criticality,
                    "repair_confidence": case.repair_confidence,
                    "healing_decision": case.healing_decision,
                },
            )

        run_summary = await classifier.classify_run(classified, app_url)
        next_actions = run_summary.get("next_actions", [])

        completed_at = datetime.now(timezone.utc).isoformat()
        await sqlite.update_run(run_id, "completed", classified, completed_at, next_actions)
        _log.info(
            "run_transition run_id=%s from_status=%s to_status=%s",
            run_id,
            "running",
            "completed",
            extra={"run_id": run_id, "from_status": "running", "to_status": "completed"},
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
                flow_ids=[f.flow_id for f in flows],
            )
        except Exception:
            pass  # Calibration recording is best-effort; never block run completion
    except asyncio.TimeoutError:
        await sqlite.update_run(
            run_id=run_id,
            status="failed",
            cases=[],
            completed_at=None,
            next_actions=[],
        )
        _log.warning(
            "run_transition run_id=%s from_status=%s to_status=%s reason=%s",
            run_id,
            "running",
            "failed",
            "run_timeout",
            extra={
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
    except asyncio.CancelledError:
        await sqlite.update_run(
            run_id=run_id,
            status="cancelled",
            cases=[],
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
        raise
    except Exception as e:
        await sqlite.update_run(
            run_id=run_id,
            status="failed",
            cases=[],
            completed_at=None,
            next_actions=[],
        )
        _log.warning(
            "run_transition run_id=%s from_status=%s to_status=%s reason=%s",
            run_id,
            "running",
            "failed",
            "unhandled_exception",
            extra={
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
