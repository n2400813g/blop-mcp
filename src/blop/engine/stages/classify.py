"""Stage 4: CLASSIFY — score failures, assign taxonomy + severity."""

from __future__ import annotations

from typing import Any

from blop.engine.errors import BLOP_STAGE_CLASSIFY_FAILED, StageError
from blop.engine.pipeline import RunContext


async def classify_run(cases: list[Any]) -> list[Any]:
    """Thin wrapper so tests can patch at this module's import path."""
    from blop.engine.classifier import classify_run as _c

    return await _c(cases)


class ClassifyStage:
    async def run(self, ctx: RunContext) -> None:
        ctx.bus.emit(
            "CLASSIFY",
            "CLASSIFY_START",
            f"Classifying {len(ctx.step_results)} case(s)",
        )
        try:
            classified = await classify_run(ctx.step_results)
            ctx.classified_cases = classified
            blocker_count = sum(1 for c in classified if getattr(c, "severity", "") == "BLOCKER")
            ctx.bus.emit(
                "CLASSIFY",
                "CLASSIFY_OK",
                f"Classification complete: {blocker_count} blocker(s) of {len(classified)} case(s)",
                {"total_cases": len(classified), "blockers": blocker_count},
            )
        except Exception as exc:
            ctx.bus.emit("CLASSIFY", "CLASSIFY_FAIL", f"Classification failed: {exc}")
            raise StageError(
                stage="CLASSIFY",
                code=BLOP_STAGE_CLASSIFY_FAILED,
                message=f"Failure classification failed: {exc}",
                likely_cause="LLM API error during classification or malformed case data.",
                suggested_fix=(
                    "Check GOOGLE_API_KEY / BLOP_LLM_PROVIDER is set and the API is reachable. "
                    "Classification failures are usually transient — retry the run."
                ),
                retry_safe=True,
                details={"error": str(exc)},
            ) from exc
