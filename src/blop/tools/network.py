"""Network mocking/routing tools with canonical scope-aware contracts."""

from __future__ import annotations

import logging
from typing import Literal, Optional

from blop.engine.browser_session_manager import SESSION_MANAGER

RouteScope = Literal["compat_session", "regression_replay"]
RouteAction = Literal["fulfill", "abort", "continue"]

_active_routes: list[dict] = []
logger = logging.getLogger(__name__)


def _route_error(message: str, **details) -> dict:
    payload = {"error": message}
    payload.update(details)
    return payload


def _headers_dict_to_list(headers: Optional[dict[str, str]]) -> list[str]:
    if not headers:
        return []
    out: list[str] = []
    for key, value in headers.items():
        out.append(f"{key}: {value}")
    return out


def _headers_list_to_dict(headers: list[str]) -> dict[str, str]:
    out: dict[str, str] = {}
    for item in headers:
        if ":" not in item:
            continue
        k, v = item.split(":", 1)
        out[k.strip()] = v.strip()
    return out


async def route_register(
    scope: RouteScope,
    pattern: str,
    action: RouteAction = "fulfill",
    status: int = 200,
    body: Optional[str] = None,
    content_type: str = "application/json",
    headers: Optional[dict[str, str]] = None,
    times: Optional[int] = None,
    name: Optional[str] = None,
    run_id: Optional[str] = None,
) -> dict:
    """Register a route mock in compat session or regression replay scope."""
    if action != "fulfill":
        return _route_error("Only action='fulfill' is currently supported", action=action, scope=scope)

    if scope == "compat_session":
        result = await SESSION_MANAGER.route_add(
            pattern=pattern,
            status=status,
            body=body,
            content_type=content_type,
            headers=_headers_dict_to_list(headers),
        )
        if "error" in result:
            return result
        return {
            "status": "registered",
            "scope": scope,
            "pattern": pattern,
            "action": action,
            "name": name,
            "run_id": run_id,
            "times": times,
        }

    if scope == "regression_replay":
        _MAX_ROUTES = 200
        if len(_active_routes) >= _MAX_ROUTES:
            return _route_error(f"Route limit reached ({_MAX_ROUTES}). Call route_clear first.")
        route = {
            "pattern": pattern,
            "status": status,
            "body": body or "",
            "content_type": content_type,
            "headers": headers or {},
            "action": action,
            "times": times,
            "name": name,
            "run_id": run_id,
        }
        _active_routes.append(route)
        return {
            "status": "registered",
            "scope": scope,
            "pattern": pattern,
            "action": action,
            "mock_status": status,
            "active_routes": len(_active_routes),
            "name": name,
            "run_id": run_id,
            "times": times,
        }

    return _route_error(f"Unsupported scope '{scope}'")


async def route_list(
    scope: RouteScope,
    run_id: Optional[str] = None,
) -> dict:
    """List active route mocks for a scope."""
    if scope == "compat_session":
        result = await SESSION_MANAGER.route_list()
        routes = []
        for route in result.get("routes", []):
            routes.append(
                {
                    "pattern": route.get("pattern"),
                    "action": "fulfill",
                    "status": route.get("status"),
                    "body": route.get("body", ""),
                    "content_type": route.get("content_type", "application/json"),
                    "headers": _headers_list_to_dict(route.get("headers", [])),
                }
            )
        return {"scope": scope, "count": len(routes), "routes": routes}

    if scope == "regression_replay":
        routes = [r for r in _active_routes if run_id is None or r.get("run_id") == run_id]
        return {"scope": scope, "count": len(routes), "routes": list(routes), "run_id": run_id}

    return _route_error(f"Unsupported scope '{scope}'")


async def route_clear(
    scope: RouteScope,
    pattern: Optional[str] = None,
    name: Optional[str] = None,
    run_id: Optional[str] = None,
) -> dict:
    """Clear scoped route mocks by pattern/name/run_id or all."""
    if scope == "compat_session":
        if name is not None or run_id is not None:
            return _route_error("compat_session route_clear supports pattern only")
        return await SESSION_MANAGER.route_remove(pattern=pattern)

    if scope == "regression_replay":
        before = len(_active_routes)
        kept: list[dict] = []
        for route in _active_routes:
            remove = True
            if pattern is not None and route.get("pattern") != pattern:
                remove = False
            if name is not None and route.get("name") != name:
                remove = False
            if run_id is not None and route.get("run_id") != run_id:
                remove = False
            if not remove:
                kept.append(route)
        _active_routes[:] = kept
        removed = before - len(_active_routes)
        return {
            "status": "cleared",
            "scope": scope,
            "removed_count": removed,
            "remaining_count": len(_active_routes),
            "pattern": pattern,
            "name": name,
            "run_id": run_id,
        }

    return _route_error(f"Unsupported scope '{scope}'")


async def mock_network_route(
    pattern: str,
    status: int = 200,
    body: Optional[str] = None,
    content_type: str = "application/json",
) -> dict:
    """Legacy wrapper for regression replay route mocks."""
    return await route_register(
        scope="regression_replay",
        pattern=pattern,
        action="fulfill",
        status=status,
        body=body,
        content_type=content_type,
    )


async def clear_network_routes() -> dict:
    """Legacy wrapper for clearing regression replay route mocks."""
    result = await route_clear(scope="regression_replay")
    if "error" in result:
        return result
    return {"status": "cleared", "removed_count": result.get("removed_count", 0)}


def get_active_routes() -> list[dict]:
    """Return currently registered regression replay route mocks."""
    return list(_active_routes)


async def apply_routes_to_context(context) -> int:
    """Apply all regression replay route mocks to a Playwright browser context."""
    applied = 0
    for route in _active_routes:
        try:
            pattern = route["pattern"]
            status = route["status"]
            body = route["body"]
            content_type = route["content_type"]
            headers = route.get("headers", {})

            async def _handler(route_obj, *, _status=status, _body=body, _ct=content_type, _headers=headers):
                await route_obj.fulfill(
                    status=_status,
                    body=_body,
                    content_type=_ct,
                    headers=_headers,
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
