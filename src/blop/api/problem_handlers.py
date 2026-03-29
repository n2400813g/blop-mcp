"""RFC 7807-style ``application/problem+json`` handlers for the HTTP server."""

from __future__ import annotations

from starlette.requests import Request
from starlette.responses import JSONResponse, Response

PROBLEM_RATE_LIMIT_TYPE = "tag:blop.dev,2026:problem:rate-limit-exceeded"


async def rate_limit_exceeded_handler(request: Request, exc: Exception) -> Response:
    """Return RFC 7807 problem details for HTTP 429; preserve slowapi rate-limit headers."""
    from slowapi.errors import RateLimitExceeded

    if not isinstance(exc, RateLimitExceeded):
        raise exc
    limiter = request.app.state.limiter
    vrl = getattr(request.state, "view_rate_limit", None)
    detail = str(exc.detail) if getattr(exc, "detail", None) else "Rate limit exceeded"
    payload = {
        "type": PROBLEM_RATE_LIMIT_TYPE,
        "title": "Rate limit exceeded",
        "status": 429,
        "detail": detail,
        "instance": request.url.path,
        "blop_code": "BLOP_HTTP_RATE_LIMIT_EXCEEDED",
    }
    resp = JSONResponse(payload, status_code=429, media_type="application/problem+json")
    return limiter._inject_headers(resp, vrl)
