"""Network mocking/routing tools — intercept and mock HTTP requests during testing."""
from __future__ import annotations

import logging
from typing import Optional

_active_routes: list[dict] = []
logger = logging.getLogger(__name__)


async def mock_network_route(
    pattern: str,
    status: int = 200,
    body: Optional[str] = None,
    content_type: str = "application/json",
) -> dict:
    """Register a network route mock for use during regression runs.

    Args:
        pattern: URL pattern to intercept (glob or regex)
        status: HTTP status code to respond with
        body: Response body string
        content_type: Response content type
    """
    route = {
        "pattern": pattern,
        "status": status,
        "body": body or "",
        "content_type": content_type,
    }
    _active_routes.append(route)
    return {
        "status": "registered",
        "pattern": pattern,
        "mock_status": status,
        "active_routes": len(_active_routes),
    }


async def clear_network_routes() -> dict:
    """Remove all registered network route mocks."""
    count = len(_active_routes)
    _active_routes.clear()
    return {"status": "cleared", "removed_count": count}


def get_active_routes() -> list[dict]:
    """Return currently registered route mocks (for use by the regression engine)."""
    return list(_active_routes)


async def apply_routes_to_context(context) -> int:
    """Apply all active route mocks to a Playwright browser context. Returns count applied."""
    applied = 0
    for route in _active_routes:
        try:
            pattern = route["pattern"]
            status = route["status"]
            body = route["body"]
            content_type = route["content_type"]

            async def _handler(route_obj, *, _status=status, _body=body, _ct=content_type):
                await route_obj.fulfill(
                    status=_status,
                    body=_body,
                    content_type=_ct,
                )

            await context.route(pattern, _handler)
            applied += 1
        except Exception as e:
            logger.exception(
                "Failed to apply network route mock pattern=%r status=%r route=%r error=%s",
                route.get("pattern"),
                route.get("status"),
                route,
                e,
            )
    return applied
