"""LLM factory: supports google (Gemini), anthropic, and openai backends.

Usage:
    from blop.engine.llm_factory import make_planning_llm, make_agent_llm, make_message

    llm = make_planning_llm(temperature=0.3, max_output_tokens=2000)
    response = await llm.ainvoke([make_message(prompt)])
    text = str(response.content)
"""

from __future__ import annotations

import os
from typing import Any

_ROLE_ENV_MAP = {
    "agent": "BLOP_AGENT_LLM_MODEL",
    "planner": "BLOP_PLANNER_LLM_MODEL",
    "repair": "BLOP_REPAIR_LLM_MODEL",
    "classifier": "BLOP_CLASSIFIER_LLM_MODEL",
    "summary": "BLOP_CLASSIFIER_LLM_MODEL",
    "vision": "BLOP_VISION_LLM_MODEL",
    "assertion": "BLOP_VISION_LLM_MODEL",
}

_DEFAULT_MODELS = {
    "google": {
        "agent": "gemini-2.5-flash",
        "planner": "gemini-2.5-flash",
        "repair": "gemini-2.5-flash",
        "classifier": "gemini-2.5-flash",
        "summary": "gemini-2.5-flash",
        "vision": "gemini-2.5-flash",
        "assertion": "gemini-2.5-flash",
    },
    "anthropic": {
        "agent": "claude-3-5-sonnet-20241022",
        "planner": "claude-3-5-haiku-20241022",
        "repair": "claude-3-5-sonnet-20241022",
        "classifier": "claude-3-5-haiku-20241022",
        "summary": "claude-3-5-haiku-20241022",
        "vision": "claude-3-5-sonnet-20241022",
        "assertion": "claude-3-5-sonnet-20241022",
    },
    "openai": {
        "agent": "gpt-4o",
        "planner": "gpt-4o-mini",
        "repair": "gpt-4o",
        "classifier": "gpt-4o-mini",
        "summary": "gpt-4o-mini",
        "vision": "gpt-4o",
        "assertion": "gpt-4o",
    },
}


def resolve_llm_model(role: str, provider: str | None = None) -> str:
    provider_name = (provider or os.getenv("BLOP_LLM_PROVIDER", "google")).lower()
    role_name = (role or "planner").strip().lower()
    env_name = _ROLE_ENV_MAP.get(role_name)
    if env_name and os.getenv(env_name):
        return os.getenv(env_name, "")
    if os.getenv("BLOP_LLM_MODEL"):
        return os.getenv("BLOP_LLM_MODEL", "")
    return _DEFAULT_MODELS.get(provider_name, _DEFAULT_MODELS["google"]).get(
        role_name,
        _DEFAULT_MODELS.get(provider_name, _DEFAULT_MODELS["google"])["planner"],
    )


def _make_chat_model(
    *,
    role: str,
    temperature: float,
    max_output_tokens: int | None = None,
) -> Any:
    provider = os.getenv("BLOP_LLM_PROVIDER", "google").lower()
    model = resolve_llm_model(role, provider)

    if provider == "anthropic":
        from langchain_anthropic import ChatAnthropic  # type: ignore[import-untyped]

        api_key = os.getenv("ANTHROPIC_API_KEY", "") or None
        kwargs = {"model": model, "api_key": api_key, "temperature": temperature}
        if max_output_tokens is not None:
            kwargs["max_tokens"] = max_output_tokens
        return ChatAnthropic(**kwargs)

    if provider == "openai":
        from langchain_openai import ChatOpenAI  # type: ignore[import-untyped]

        api_key = os.getenv("OPENAI_API_KEY", "") or None
        kwargs = {"model": model, "api_key": api_key, "temperature": temperature}
        if max_output_tokens is not None:
            kwargs["max_tokens"] = max_output_tokens
        return ChatOpenAI(**kwargs)

    # Default: Google Gemini via browser_use
    from browser_use.llm import ChatGoogle

    api_key = os.getenv("GOOGLE_API_KEY", "")
    kwargs = {"model": model, "api_key": api_key, "temperature": temperature}
    if max_output_tokens is not None:
        kwargs["max_output_tokens"] = max_output_tokens
    return ChatGoogle(**kwargs)


def make_planning_llm(
    temperature: float = 0.3,
    max_output_tokens: int = 2000,
    *,
    role: str = "planner",
) -> Any:
    """Return a chat model for planning/classification/remediation calls."""
    return _make_chat_model(
        role=role,
        temperature=temperature,
        max_output_tokens=max_output_tokens,
    )


def make_agent_llm(*, role: str = "agent", temperature: float = 0.7) -> Any:
    """Return a chat model for use as the Browser-Use agent backbone."""
    return _make_chat_model(role=role, temperature=temperature, max_output_tokens=None)


def make_message(prompt: str) -> Any:
    """Return the appropriate message type for the configured LLM provider.

    Google/browser_use uses UserMessage; anthropic/openai use HumanMessage.
    Falls back to UserMessage so existing google paths stay unchanged.
    """
    provider = os.getenv("BLOP_LLM_PROVIDER", "google").lower()
    if provider in ("anthropic", "openai"):
        from langchain_core.messages import HumanMessage

        return HumanMessage(content=prompt)
    # google (browser_use)
    from browser_use.llm.messages import UserMessage

    return UserMessage(content=prompt)
