"""Optional HTTP SSE streaming server for blop run health events + REST /v1 API.

Install optional deps:
    pip install blop-mcp[server]

Run:
    blop-http
    # or: python -m blop.server_http

Endpoints:
    GET /runs/{run_id}/stream  — SSE stream of run health events
    GET /health                — liveness probe
    GET /metrics               — Prometheus text when prometheus-client is installed
    /v1/*                      — API key optional when BLOP_HTTP_API_KEY unset (see docs)
"""

from __future__ import annotations

import asyncio
import os
import sys
from contextlib import asynccontextmanager

try:
    import uvicorn
    from fastapi import FastAPI
    from sse_starlette.sse import EventSourceResponse

    _HAS_DEPS = True
except ImportError:
    _HAS_DEPS = False

if _HAS_DEPS:

    @asynccontextmanager
    async def _lifespan(app: FastAPI):
        from blop import config
        from blop.storage.sqlite import init_db
        from blop.tools import regression

        if not (config.BLOP_HTTP_API_KEY or "").strip():
            print(
                "WARNING: BLOP_HTTP_API_KEY is unset — /v1 REST API is unauthenticated",
                file=sys.stderr,
            )
        await init_db()
        await regression.resume_incomplete_runs()
        yield

    app = FastAPI(title="blop HTTP server", version="0.3.0", lifespan=_lifespan)

    try:
        from slowapi.errors import RateLimitExceeded
        from slowapi.middleware import SlowAPIMiddleware

        from blop.api.problem_handlers import rate_limit_exceeded_handler
        from blop.api.v1.rate_limit import http_limiter

        app.state.limiter = http_limiter
        app.add_exception_handler(RateLimitExceeded, rate_limit_exceeded_handler)
        app.add_middleware(SlowAPIMiddleware)
    except ImportError:
        pass

    from blop.api.v1.router import router as v1_router

    app.include_router(v1_router, prefix="/v1")

    async def _run_event_stream(run_id: str):
        """Yield SSE events until the run reaches a terminal state."""
        from blop.storage.sqlite import get_run, list_run_health_events

        seen: set[str] = set()
        while True:
            run = await get_run(run_id)
            events = await list_run_health_events(run_id, limit=500)
            for event in events:
                eid = event["event_id"]
                if eid not in seen:
                    seen.add(eid)
                    yield {"event": event["event_type"], "data": str(event["payload"])}
            if run and run.get("status") in ("completed", "failed", "cancelled", "interrupted"):
                yield {"event": "terminal", "data": run["status"]}
                break
            await asyncio.sleep(1.0)

    @app.get("/runs/{run_id}/stream")
    async def stream_run(run_id: str):
        """Stream SSE health events for a run until it reaches a terminal state."""
        return EventSourceResponse(_run_event_stream(run_id))

    @app.get("/health")
    async def health():
        return {"status": "ok"}

    @app.get("/metrics")
    async def metrics():
        from fastapi.responses import Response

        from blop.engine.metrics import metrics_text

        body = metrics_text()
        if body is None:
            return Response(
                status_code=503,
                content="# prometheus_client not installed; pip install blop-mcp[server]\n",
                media_type="text/plain; charset=utf-8",
            )
        return Response(content=body, media_type="text/plain; version=0.0.4; charset=utf-8")


def run() -> int:
    if not _HAS_DEPS:
        print("fastapi, uvicorn, and sse-starlette are required.\nInstall with: pip install blop-mcp[server]")
        return 1
    host = os.getenv("BLOP_HTTP_HOST", "0.0.0.0")
    port = int(os.getenv("BLOP_HTTP_PORT", "8765"))
    uvicorn.run(app, host=host, port=port)
    return 0


if __name__ == "__main__":
    import sys

    sys.exit(run())
