"""Stage 5: REPORT — produce SHIP / INVESTIGATE / BLOCK decision."""

from __future__ import annotations

from typing import Any

from blop.engine.errors import BLOP_STAGE_REPORT_FAILED, StageError
from blop.engine.pipeline import RunContext


def build_report(run_id: str, classified_cases: list[Any]) -> dict[str, Any]:
    """Thin wrapper so tests can patch at this module's import path."""
    from blop.reporting.results import build_report as _b

    return _b(run_id, classified_cases)


class ReportStage:
    async def run(self, ctx: RunContext) -> None:
        try:
            report = build_report(ctx.run_id, ctx.classified_cases)
            ctx.report = report
            decision = report.get("decision", "UNKNOWN")
            ctx.bus.emit(
                "REPORT",
                "REPORT_READY",
                f"Report ready — decision: {decision}",
                {
                    "decision": decision,
                    "run_id": ctx.run_id,
                    "blocker_count": report.get("blocker_count", 0),
                    "total_cases": report.get("total_cases", len(ctx.classified_cases)),
                },
            )
        except Exception as exc:
            raise StageError(
                stage="REPORT",
                code=BLOP_STAGE_REPORT_FAILED,
                message=f"Report generation failed: {exc}",
                likely_cause="Missing or malformed classified case data.",
                suggested_fix="Ensure CLASSIFY stage completed successfully, then retry.",
                retry_safe=True,
                details={"error": str(exc)},
            ) from exc
