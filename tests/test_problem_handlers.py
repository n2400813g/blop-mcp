"""RFC 7807 rate-limit handler for blop-http."""

from __future__ import annotations

import json
from unittest.mock import MagicMock

import pytest

pytest.importorskip("slowapi")


@pytest.mark.asyncio
async def test_rate_limit_problem_details_json():
    from limits import parse
    from slowapi.errors import RateLimitExceeded
    from slowapi.wrappers import Limit

    from blop.api.problem_handlers import rate_limit_exceeded_handler

    lim_item = parse("1/minute")
    limit = Limit(lim_item, lambda: "u", None, False, None, None, None, 1, False)
    exc = RateLimitExceeded(limit)

    request = MagicMock()
    request.url.path = "/v1/releases/x/checks"
    request.state.view_rate_limit = None
    request.app.state.limiter = MagicMock()
    request.app.state.limiter._inject_headers = lambda resp, vrl: resp

    resp = await rate_limit_exceeded_handler(request, exc)
    assert resp.status_code == 429
    assert "application/problem+json" in (resp.media_type or "")
    body = json.loads(resp.body.decode())
    assert body["title"] == "Rate limit exceeded"
    assert body["status"] == 429
    assert body["blop_code"] == "BLOP_HTTP_RATE_LIMIT_EXCEEDED"
    assert "type" in body and "instance" in body
