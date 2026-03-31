"""Stage 3: EXECUTE — replay recorded flows and emit per-step health events."""

from __future__ import annotations

from typing import Any

from blop.engine.errors import BLOP_STAGE_EXECUTE_FAILED, StageError
from blop.engine.pipeline import RunContext


async def run_flows(
    flow_ids: list[str],
    app_url: str,
    storage_state: str | None,
) -> list[Any]:
    """Thin wrapper so tests can patch at this module's import path."""
    from blop.engine.regression import run_flows as _run_flows

    return await _run_flows(flow_ids, app_url=app_url, storage_state=storage_state)


class ExecuteStage:
    async def run(self, ctx: RunContext) -> None:
        ctx.bus.emit(
            "EXECUTE",
            "EXECUTE_START",
            f"Starting replay of {len(ctx.flow_ids)} flow(s)",
            {"flow_count": len(ctx.flow_ids), "flow_ids": ctx.flow_ids},
        )

        try:
            cases = await run_flows(
                ctx.flow_ids,
                app_url=ctx.validated_url or ctx.app_url,
                storage_state=ctx.auth_state,
            )
        except Exception as exc:
            ctx.bus.emit("EXECUTE", "EXECUTE_DONE", f"Execution failed: {exc}")
            raise StageError(
                stage="EXECUTE",
                code=BLOP_STAGE_EXECUTE_FAILED,
                message=f"Flow execution failed: {exc}",
                likely_cause="Browser crashed, network error, or Playwright timeout during replay.",
                suggested_fix=(
                    "Check BLOP_STEP_TIMEOUT_SECS (default 30s), verify the app is reachable, "
                    "and check for browser console errors in the run artifacts."
                ),
                retry_safe=True,
                details={"error": str(exc)},
            ) from exc

        for case in cases:
            self._emit_case_events(ctx, case)
            ctx.step_results.append(case)

        passed = sum(1 for c in cases if getattr(c, "status", None) == "passed")
        failed = len(cases) - passed
        ctx.bus.emit(
            "EXECUTE",
            "EXECUTE_DONE",
            f"Execution complete: {passed} passed, {failed} failed",
            {"total": len(cases), "passed": passed, "failed": failed},
        )

    def _emit_case_events(self, ctx: RunContext, case: Any) -> None:
        for i, step in enumerate(getattr(case, "step_results", []) or []):
            status = getattr(step, "status", "unknown")
            healed = getattr(step, "healed", False)
            selector = getattr(step, "selector", "")
            action = getattr(step, "action", "")

            if status == "passed" and not healed:
                ctx.bus.emit(
                    "EXECUTE",
                    "STEP_OK",
                    f"Step {i + 1} passed ({action})",
                    {"step_index": i, "selector": selector, "action": action},
                )
            elif healed:
                ctx.bus.emit(
                    "EXECUTE",
                    "STEP_HEALED",
                    f"Step {i + 1} healed ({action})",
                    {"step_index": i, "selector": selector, "action": action},
                )
            else:
                ctx.bus.emit(
                    "EXECUTE",
                    "STEP_FAIL",
                    f"Step {i + 1} failed ({action} on {selector!r})",
                    {
                        "step_index": i,
                        "selector": selector,
                        "action": action,
                        "healing_attempted": getattr(step, "healing_attempted", False),
                        "healing_result": "FAILED",
                        "screenshot_ref": getattr(step, "screenshot_path", None),
                        "console_errors": getattr(step, "console_errors", []),
                    },
                )
