"""Tests for SDK-aware LLM failure classification."""

from __future__ import annotations

import httpx
import pytest

from blop.engine.llm_exceptions import classify_llm_failure


def test_classify_httpx_429():
    req = httpx.Request("GET", "https://example.com/")
    resp = httpx.Response(429, request=req)
    exc = httpx.HTTPStatusError("rate limited", request=req, response=resp)
    w, r = classify_llm_failure(exc)
    assert w and r


def test_classify_httpx_503():
    req = httpx.Request("GET", "https://example.com/")
    resp = httpx.Response(503, request=req)
    exc = httpx.HTTPStatusError("unavailable", request=req, response=resp)
    w, r = classify_llm_failure(exc)
    assert w and r


def test_classify_google_resource_exhausted_by_name():
    class ResourceExhausted(Exception):
        pass

    ResourceExhausted.__module__ = "google.api_core.exceptions"
    exc = ResourceExhausted("quota")
    w, r = classify_llm_failure(exc)
    assert w and r


def test_classify_openai_rate_limit_by_name():
    class RateLimitError(Exception):
        pass

    RateLimitError.__module__ = "openai"
    exc = RateLimitError("slow down")
    w, r = classify_llm_failure(exc)
    assert w and r


def test_classify_openai_timeout_retriable_not_circuit_only():
    class APITimeoutError(Exception):
        pass

    APITimeoutError.__module__ = "openai"
    exc = APITimeoutError("timeout")
    w, r = classify_llm_failure(exc)
    assert not w and r


@pytest.mark.parametrize(
    "msg,worthy",
    [
        ("429 too many", True),
        ("something else", False),
    ],
)
def test_text_fallback(msg, worthy):
    w, _ = classify_llm_failure(RuntimeError(msg))
    assert w is worthy
