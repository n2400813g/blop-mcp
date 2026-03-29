"""HTTP rate limits for ``blop-http`` (slowapi when installed)."""

from __future__ import annotations

from blop import config


def _default_limit() -> str:
    n = config.BLOP_HTTP_RATE_LIMIT_PER_MIN
    if n <= 0:
        return "60/minute"
    return f"{n}/minute"


def _llm_heavy_limit() -> str:
    n = config.BLOP_HTTP_LLM_ROUTE_RATE_LIMIT_PER_MIN
    if n <= 0:
        return "10/minute"
    return f"{n}/minute"


try:
    from slowapi import Limiter
    from slowapi.util import get_remote_address

    http_limiter = Limiter(key_func=get_remote_address, default_limits=[_default_limit()])
    LLM_HEAVY_ROUTE_LIMIT = _llm_heavy_limit()
except ImportError:

    class _NoOpLimiter:
        def limit(self, *_args, **_kwargs):
            def decorator(fn):
                return fn

            return decorator

    http_limiter = _NoOpLimiter()  # type: ignore[assignment]
    LLM_HEAVY_ROUTE_LIMIT = _llm_heavy_limit()
