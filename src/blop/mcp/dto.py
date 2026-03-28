"""Agent-facing DTOs for MCP context and atomic browser tools."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field


class WorkspaceContextDTO(BaseModel):
    workspace_id: str
    environment: str
    exploration_profile: str
    resource_uris: dict[str, str] = Field(
        default_factory=dict,
        description="Stable blop:// resource keys → URIs",
    )
    primary_tools: list[str] = Field(
        default_factory=list,
        description="Suggested tool names for the release-confidence loop",
    )
    recommended_next_action_hint: str = Field(
        default="",
        description="Short hint for the next agent step (preflight → context → release check)",
    )
    hosted_sync: dict[str, Any] = Field(
        default_factory=dict,
        description="Optional BLOP_HOSTED_URL and api_token_configured (bool)",
    )


class ReleaseContextDTO(BaseModel):
    release_id: str
    run_id: str | None = None
    app_url: str | None = None
    created_at: str | None = None
    decision: str | None = None
    risk: dict[str, Any] | None = None
    confidence: dict[str, Any] | None = None
    blocker_count: int | None = None
    blocker_journey_names: list[str] = Field(default_factory=list)
    critical_journey_failures: int | None = None
    top_actions: list[dict[str, Any]] = Field(default_factory=list)
    context_graph_summary: dict[str, Any] | None = None
    resource_links: dict[str, str] = Field(
        default_factory=dict,
        description="blop:// URIs for brief, artifacts, incidents",
    )
    recommended_next_action_hint: str = Field(
        default="",
        description="What to call next (e.g. poll run, read brief, triage)",
    )
    error: str | None = None


class JourneyListDTO(BaseModel):
    app_url: str | None = None
    release_id: str | None = None
    journeys: list[dict[str, Any]] = Field(default_factory=list)
    total: int = 0
    stale_release_gating_count: int = 0
    workflow_hint: str = ""
    resource_links: dict[str, str] = Field(
        default_factory=dict,
        description="Stable URIs for full journey inventory",
    )
    recommended_next_action_hint: str = Field(
        default="",
        description="e.g. refresh stale recordings or run release check",
    )


class ReleaseAndJourneysDTO(BaseModel):
    release: ReleaseContextDTO
    journeys: JourneyListDTO


class PrdSummaryDTO(BaseModel):
    prd_source: Literal["none", "recorded_flows", "release_brief"]
    scope: Literal["journey", "release"]
    release_id: str | None = None
    journey_id: str | None = None
    app_url: str | None = None
    key_requirements: list[str] = Field(default_factory=list)
    critical_journeys: list[str] = Field(default_factory=list)
    risk_notes: list[str] = Field(default_factory=list)
    acceptance_criteria: list[str] = Field(default_factory=list)
    flow_goal: str | None = None
    intent_contract: dict[str, Any] | None = None


class UxTaxonomyDTO(BaseModel):
    version: str = "1"
    criticality_hints: dict[str, str]
    archetype_hints: dict[str, str]


class PerformStepResultDTO(BaseModel):
    action: str
    status: str
    detail: dict[str, Any] = Field(default_factory=dict)


class CaptureArtifactResultDTO(BaseModel):
    kind: str
    path: str | None = None
    run_id: str | None = None
    note: str | None = None


class RunObservationResultDTO(BaseModel):
    run_id: str
    observation_key: str
    updated: bool = True
