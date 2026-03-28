"""Minimal BrowserGym-style task: observation / action / transition without a browser.

Exercises a tiny MDP: home → cart → checkout → done. Policies are pure functions so CI
needs no Playwright, BrowserGym, or network.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import pytest

pytestmark = pytest.mark.eval_harness


@dataclass
class MiniWebObs:
    """Observation (BrowserGym-like compact dict)."""

    step_index: int
    url: str
    screen: str
    dom_excerpt: str = ""


@dataclass
class MiniWebEnv:
    """Abstract browser task: reset + step."""

    max_steps: int = 12
    _state: str = field(default="home", init=False)
    _i: int = field(default=0, init=False)

    def reset(self) -> MiniWebObs:
        self._state = "home"
        self._i = 0
        return self._obs()

    def _obs(self) -> MiniWebObs:
        return MiniWebObs(
            step_index=self._i,
            url=f"https://example.test/{self._state}",
            screen=self._state,
            dom_excerpt=f"<main data-screen={self._state!r}/>",
        )

    def step(self, action: str) -> tuple[MiniWebObs, float, bool, dict[str, Any]]:
        """Returns (obs, reward, terminated, info)."""
        reward = 0.0
        terminated = False
        info: dict[str, Any] = {}
        self._i += 1

        if self._state == "home" and action == "goto_cart":
            self._state = "cart"
            reward = 0.1
        elif self._state == "cart" and action == "checkout":
            self._state = "checkout"
            reward = 0.3
        elif self._state == "checkout" and action == "confirm":
            self._state = "done"
            reward = 1.0
            terminated = True
        else:
            info["invalid_action"] = True

        if self._i >= self.max_steps and not terminated:
            terminated = True
            info["truncated"] = True

        return self._obs(), reward, terminated, info


def scripted_policy(obs: MiniWebObs) -> str:
    """Deterministic policy that completes the flow."""
    if obs.screen == "home":
        return "goto_cart"
    if obs.screen == "cart":
        return "checkout"
    if obs.screen == "checkout":
        return "confirm"
    return "noop"


def test_mini_web_env_happy_path():
    env = MiniWebEnv()
    obs = env.reset()
    total = 0.0
    for _ in range(20):
        a = scripted_policy(obs)
        obs, r, done, info = env.step(a)
        total += r
        if done and obs.screen == "done":
            assert total >= 1.0
            return
    raise AssertionError("episode did not terminate at done")


def test_policy_is_pure_function():
    """Regression hook: orchestration policies should be testable without I/O."""
    obs = MiniWebObs(0, "https://x", "home")
    assert scripted_policy(obs) == "goto_cart"
