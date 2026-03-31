"""Stage 2: AUTH — resolve and validate the auth profile, acquire storage_state."""

from __future__ import annotations

from blop.engine.errors import BLOP_AUTH_PROFILE_NOT_FOUND, StageError
from blop.engine.pipeline import RunContext


async def resolve_storage_state(profile_name: str, app_url: str) -> str | None:
    """Thin wrapper so tests can patch at this module's import path."""
    from blop.engine.auth import resolve_storage_state_for_profile

    return await resolve_storage_state_for_profile(profile_name, allow_auto_env=False)


class AuthStage:
    async def run(self, ctx: RunContext) -> None:
        profile = ctx.profile_name
        ctx.bus.emit("AUTH", "AUTH_START", f"Resolving auth profile: {profile or '(none)'}")

        if profile is None:
            ctx.auth_state = None
            ctx.bus.emit("AUTH", "AUTH_OK", "No auth profile required — proceeding unauthenticated")
            return

        try:
            state = await resolve_storage_state(profile, ctx.validated_url or ctx.app_url)
            ctx.auth_state = state
            ctx.bus.emit("AUTH", "AUTH_OK", f"Auth profile '{profile}' resolved successfully")
        except Exception as exc:
            ctx.bus.emit("AUTH", "AUTH_FAIL", f"Auth resolution failed for '{profile}': {exc}")
            raise StageError(
                stage="AUTH",
                code=BLOP_AUTH_PROFILE_NOT_FOUND,
                message=f"Auth profile '{profile}' could not be resolved: {exc}",
                likely_cause=(
                    f"Profile '{profile}' was not created, its storage_state file is missing, "
                    "or the session has expired."
                ),
                suggested_fix=(
                    f"Run save_auth_profile with profile_name='{profile}' to create the profile, "
                    f"or run capture_auth_session to re-capture a fresh session for '{profile}'."
                ),
                retry_safe=False,
                details={"profile_name": profile, "error": str(exc)},
            ) from exc
