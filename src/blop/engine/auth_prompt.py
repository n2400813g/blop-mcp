"""Safe auth guidance for agent prompts.

This module intentionally avoids including raw credential values in any prompt
text that may be sent to third-party LLM providers.
"""

from __future__ import annotations

from blop.config import LOGIN_URL, TEST_AUTH_URL, TEST_PASSWORD, TEST_USERNAME


def append_runtime_auth_guidance(task: str) -> str:
    """Append login guidance without exposing plaintext credentials."""
    login_url = (LOGIN_URL or TEST_AUTH_URL or "").strip()
    username = TEST_USERNAME.strip()
    password = TEST_PASSWORD.strip()
    if not (login_url and username and password):
        return task
    return (
        task
        + "\n\nIf you encounter a login page or are redirected to auth, "
        + "use the pre-configured runtime test credentials to sign in "
        + f"(login URL: {login_url}). "
        + "Do NOT create a new account."
    )
