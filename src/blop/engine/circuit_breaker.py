"""Per-provider LLM circuit breaker (quota / transient upstream failures)."""

from __future__ import annotations

import asyncio
import time
from enum import Enum

from blop.config import BLOP_LLM_CIRCUIT_COOLDOWN_SEC, BLOP_LLM_CIRCUIT_FAILURE_THRESHOLD
from blop.engine.errors import BlopError
from blop.engine.llm_exceptions import classify_llm_failure


class _State(Enum):
    CLOSED = "closed"
    OPEN = "open"
    HALF_OPEN = "half_open"


def llm_failure_is_circuit_worthy(exc: BaseException) -> bool:
    """True if the failure should increment toward opening the circuit."""
    return classify_llm_failure(exc)[0]


def llm_failure_is_retriable(exc: BaseException) -> bool:
    """True if a short backoff retry may help."""
    return classify_llm_failure(exc)[1]


class LlmCircuitBreaker:
    def __init__(self, provider: str) -> None:
        self.provider = provider.strip().lower() or "google"
        self._state = _State.CLOSED
        self._failures = 0
        self._opened_at = 0.0
        self._threshold = BLOP_LLM_CIRCUIT_FAILURE_THRESHOLD
        self._cooldown = BLOP_LLM_CIRCUIT_COOLDOWN_SEC
        self._lock = asyncio.Lock()

    async def before_call(self) -> None:
        async with self._lock:
            if self._state == _State.OPEN:
                if time.monotonic() - self._opened_at >= self._cooldown:
                    self._state = _State.HALF_OPEN
                else:
                    raise BlopError(
                        "BLOP_LLM_CIRCUIT_OPEN",
                        "LLM circuit breaker is open after repeated quota or upstream failures. "
                        "Wait for the cooldown or fix provider credentials/quotas.",
                        retryable=True,
                        details={"provider": self.provider, "cooldown_sec": self._cooldown},
                    )

    async def record_success(self) -> None:
        async with self._lock:
            self._failures = 0
            self._state = _State.CLOSED

    async def record_outcome(self, *, success: bool, circuit_worthy_failure: bool) -> None:
        async with self._lock:
            if success:
                self._failures = 0
                self._state = _State.CLOSED
                return
            if not circuit_worthy_failure:
                return
            if self._state == _State.HALF_OPEN:
                self._state = _State.OPEN
                self._opened_at = time.monotonic()
                self._failures = self._threshold
                return
            self._failures += 1
            if self._failures >= self._threshold:
                self._state = _State.OPEN
                self._opened_at = time.monotonic()


_BREAKERS: dict[str, LlmCircuitBreaker] = {}
_BREAKERS_LOCK = asyncio.Lock()


async def get_llm_circuit(provider: str) -> LlmCircuitBreaker:
    key = (provider or "google").strip().lower()
    async with _BREAKERS_LOCK:
        if key not in _BREAKERS:
            _BREAKERS[key] = LlmCircuitBreaker(key)
        return _BREAKERS[key]


def reset_llm_circuits_for_tests() -> None:
    """Clear breaker state (unit tests)."""
    _BREAKERS.clear()
