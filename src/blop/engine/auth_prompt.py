"""Safe auth guidance for agent prompts.

This module intentionally avoids including raw credential values in any prompt
text that may be sent to third-party LLM providers.
"""

from __future__ import annotations

import os


def append_runtime_auth_guidance(task: str) -> str:
    """Append login guidance without exposing plaintext credentials."""
    login_url = (os.getenv("LOGIN_URL") or os.getenv("TEST_AUTH_URL") or "").strip()
    username = (os.getenv("TEST_USERNAME") or "").strip()
    password = (os.getenv("TEST_PASSWORD") or "").strip()
    if not (login_url and username and password):
        return task
    return (
        task
        + "\n\nIf you encounter a login page or are redirected to auth, "
        + "use the pre-configured runtime test credentials to sign in "
        + f"(login URL: {login_url}). "
        + "Do NOT create a new account."
    )
