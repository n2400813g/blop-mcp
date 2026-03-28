from __future__ import annotations

from fastapi import Header, HTTPException

from blop import config


async def require_v1_api_key(
    authorization: str | None = Header(None),
    x_blop_api_key: str | None = Header(None, alias="X-Blop-Api-Key"),
) -> None:
    """Require Bearer or X-Blop-Api-Key when BLOP_HTTP_API_KEY is set."""
    expected = (config.BLOP_HTTP_API_KEY or "").strip()
    if not expected:
        return
    token: str | None = None
    if authorization and authorization.lower().startswith("bearer "):
        token = authorization[7:].strip()
    elif x_blop_api_key:
        token = x_blop_api_key.strip()
    if not token or token != expected:
        raise HTTPException(status_code=401, detail="Invalid or missing API key")
