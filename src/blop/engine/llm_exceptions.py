"""Classify LLM / transport failures for circuit breaker and retries (SDK + text heuristics)."""

from __future__ import annotations


def _text_circuit_worthy(exc: BaseException) -> bool:
    text = f"{type(exc).__name__} {exc!s}".lower()
    needles = (
        "429",
        "503",
        "502",
        "quota",
        "rate limit",
        "resourceexhausted",
        "resource exhausted",
        "overloaded",
        "unavailable",
        "too many requests",
        "exhausted",
    )
    return any(n in text for n in needles)


def _text_retriable(exc: BaseException) -> bool:
    text = f"{type(exc).__name__} {exc!s}".lower()
    return any(
        t in text
        for t in (
            "429",
            "503",
            "502",
            "rate limit",
            "unavailable",
            "overloaded",
            "timeout",
            "temporar",
        )
    )


def _http_status_from_exc(exc: BaseException) -> int | None:
    resp = getattr(exc, "response", None)
    if resp is None:
        return None
    return getattr(resp, "status_code", None)


def _classify_sdk(exc: BaseException) -> tuple[bool, bool] | None:
    """Return (circuit_worthy, retriable) when we recognize the SDK type."""
    mod = (type(exc).__module__ or "").lower()
    name = type(exc).__name__

    if name == "ResourceExhausted" and "google" in mod:
        return True, True
    if name == "TooManyRequests" and "google" in mod:
        return True, True
    if name == "ServiceUnavailable" and "google" in mod:
        return True, True

    if name == "RateLimitError" and ("anthropic" in mod or "openai" in mod):
        return True, True
    if name == "APIConnectionError" and "openai" in mod:
        return False, True
    if name == "APITimeoutError" and "openai" in mod:
        return False, True
    if name == "InternalServerError" and "openai" in mod:
        return True, True

    if name == "HTTPStatusError":
        status = _http_status_from_exc(exc)
        if status == 429:
            return True, True
        if status in (500, 502, 503, 504):
            return True, True
        if status is not None and 400 <= status < 500:
            return False, False

    return None


def classify_llm_failure(exc: BaseException) -> tuple[bool, bool]:
    """Whether the failure counts toward the LLM circuit and whether to backoff-retry."""
    hit = _classify_sdk(exc)
    if hit is not None:
        return hit
    return _text_circuit_worthy(exc), _text_retriable(exc)
