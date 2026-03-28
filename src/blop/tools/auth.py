from __future__ import annotations

from typing import Optional

from pydantic import ValidationError

from blop.engine import auth as auth_engine
from blop.schemas import AuthProfile, AuthProfileResult
from blop.storage import sqlite


async def save_auth_profile(
    profile_name: str,
    auth_type: str,
    login_url: Optional[str] = None,
    username_env: Optional[str] = "TEST_USERNAME",
    password_env: Optional[str] = "TEST_PASSWORD",
    storage_state_path: Optional[str] = None,
    cookie_json_path: Optional[str] = None,
    user_data_dir: Optional[str] = None,
) -> dict:
    try:
        profile = AuthProfile(
            profile_name=profile_name,
            auth_type=auth_type,
            login_url=login_url,
            username_env=username_env,
            password_env=password_env,
            storage_state_path=storage_state_path,
            cookie_json_path=cookie_json_path,
            user_data_dir=user_data_dir,
        )
    except ValidationError as exc:
        details = "; ".join(f"{'.'.join(str(p) for p in err.get('loc', []))}: {err.get('msg')}" for err in exc.errors())
        return {"error": f"Invalid auth profile input. {details}"}

    storage_path: Optional[str] = None
    auth_warning: Optional[str] = None
    try:
        storage_path = await auth_engine.resolve_storage_state(profile)
    except Exception as exc:
        auth_warning = (
            f"Auth profile saved but storage state could not be resolved: {exc}. "
            "Runs using this profile may fail until the issue is fixed. "
            "For SSO/OAuth, try capture_auth_session instead."
        )
    if auth_type == "env_login" and storage_path is None and auth_warning is None:
        auth_warning = (
            "Auth profile saved but no storage state was captured from the login flow. "
            "The login page may require social sign-in/SSO, the credentials may be invalid, "
            "or an additional verification step may be blocking automation. "
            "For OAuth/SSO, try capture_auth_session instead."
        )

    await sqlite.save_auth_profile(profile, storage_path)

    note = "Credentials are read from environment variables at run time"
    if auth_type != "env_login":
        note = f"Auth profile saved with type '{auth_type}'"

    result = AuthProfileResult(
        profile_name=profile_name,
        auth_type=auth_type,
        status="saved",
        note=note,
    ).model_dump()

    if auth_warning:
        result["warning"] = auth_warning
        result["status"] = "saved_with_warning"

    return result
