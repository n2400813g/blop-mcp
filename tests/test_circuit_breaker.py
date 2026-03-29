"""Tests for LLM circuit breaker."""

from __future__ import annotations

import pytest

from blop.engine.circuit_breaker import (
    LlmCircuitBreaker,
    llm_failure_is_circuit_worthy,
    llm_failure_is_retriable,
    reset_llm_circuits_for_tests,
)
from blop.engine.errors import BlopError


def test_llm_failure_heuristics():
    assert llm_failure_is_circuit_worthy(Exception("429 Too Many Requests"))
    assert llm_failure_is_circuit_worthy(Exception("quota exceeded for api"))
    assert not llm_failure_is_circuit_worthy(Exception("invalid json from model"))
    assert llm_failure_is_retriable(Exception("503 Service Unavailable"))


@pytest.mark.asyncio
async def test_circuit_opens_after_threshold_failures():
    reset_llm_circuits_for_tests()
    b = LlmCircuitBreaker("google")
    b._threshold = 3  # type: ignore[attr-defined]
    await b.before_call()
    for _ in range(3):
        await b.record_outcome(success=False, circuit_worthy_failure=True)
    with pytest.raises(BlopError) as excinfo:
        await b.before_call()
    assert excinfo.value.code == "BLOP_LLM_CIRCUIT_OPEN"


@pytest.mark.asyncio
async def test_success_resets_failures():
    reset_llm_circuits_for_tests()
    b = LlmCircuitBreaker("anthropic")
    b._threshold = 2  # type: ignore[attr-defined]
    await b.record_outcome(success=False, circuit_worthy_failure=True)
    await b.record_outcome(success=True, circuit_worthy_failure=False)
    await b.record_outcome(success=False, circuit_worthy_failure=True)
    await b.before_call()


@pytest.mark.asyncio
async def test_get_llm_circuit_singleton():
    reset_llm_circuits_for_tests()
    from blop.engine.circuit_breaker import get_llm_circuit

    a = await get_llm_circuit("openai")
    b = await get_llm_circuit("openai")
    assert a is b
