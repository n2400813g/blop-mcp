"""Shared builders for RecordedFlow and FlowStep construction."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Callable, Optional, TypedDict

from blop.schemas import FlowStep, RecordedFlow, SpaHints


class AgentStepInfo(TypedDict, total=False):
    """Normalized subset of agent-step fields used to build FlowStep entries."""

    action: str
    description: str
    step: int


def build_recorded_flow(
    *,
    flow_name: str,
    app_url: str,
    goal: str,
    steps: list[FlowStep],
    assertions_json: Optional[list[str]] = None,
    entry_url: Optional[str] = None,
    business_criticality: str = "other",
    spa_hints: Optional[SpaHints] = None,
    run_mode_override: Optional[str] = None,
) -> RecordedFlow:
    """Create a canonical RecordedFlow payload used by multiple tool paths."""
    return RecordedFlow(
        flow_name=flow_name,
        app_url=app_url,
        goal=goal,
        steps=steps,
        created_at=datetime.now(timezone.utc).isoformat(),
        assertions_json=assertions_json or [],
        entry_url=entry_url or app_url,
        business_criticality=business_criticality,
        spa_hints=spa_hints or SpaHints(),
        run_mode_override=run_mode_override,
    )


def build_steps_from_agent_actions(
    *,
    app_url: str,
    final_assertion: str,
    agent_steps: list[AgentStepInfo],
    map_action: Callable[[str], Optional[str]],
) -> list[FlowStep]:
    """Build canonical steps from agent action summaries."""
    steps: list[FlowStep] = [
        FlowStep(
            step_id=0,
            action="navigate",
            value=app_url,
            description=f"Navigate to {app_url}",
            url_after=app_url,
        )
    ]

    for step_info in agent_steps:
        action_name = step_info.get("action", "click")
        mapped = map_action(action_name)
        if not mapped:
            continue
        steps.append(
            FlowStep(
                step_id=len(steps),
                action=mapped,
                description=step_info.get("description", ""),
            )
        )

    steps.append(
        FlowStep(
            step_id=len(steps),
            action="assert",
            description=final_assertion,
            value=final_assertion,
        )
    )
    return steps
