"""Stage 1: VALIDATE — verify app_url is well-formed and configuration is sane."""

from __future__ import annotations

from blop.engine.errors import BLOP_URL_VALIDATION_FAILED, StageError
from blop.engine.pipeline import RunContext


def validate_app_url(url: str) -> str:
    """Thin wrapper so tests can patch at this module's import path."""
    from blop.config import validate_app_url as _v

    return _v(url)


class ValidateStage:
    async def run(self, ctx: RunContext) -> None:
        ctx.bus.emit("VALIDATE", "VALIDATE_START", f"Validating app_url: {ctx.app_url}")
        try:
            url = validate_app_url(ctx.app_url)
        except Exception as exc:
            ctx.bus.emit("VALIDATE", "VALIDATE_FAIL", f"URL validation failed: {exc}")
            raise StageError(
                stage="VALIDATE",
                code=BLOP_URL_VALIDATION_FAILED,
                message=str(exc),
                likely_cause=(
                    "The app_url is malformed, uses an unsupported scheme, or is blocked by BLOP_ALLOWED_URL_PATTERN."
                ),
                suggested_fix=(
                    "Check that app_url starts with http:// or https://, "
                    "is reachable from this machine, and is not in BLOP_BLOCKED_URL_PATTERNS."
                ),
                retry_safe=False,
            ) from exc

        ctx.validated_url = url.rstrip("/")
        ctx.bus.emit("VALIDATE", "VALIDATE_OK", f"URL validated: {ctx.validated_url}")
