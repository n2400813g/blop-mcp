from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

from blop.schemas import ReleaseBrief


class ProjectCreate(BaseModel):
    name: str
    repo_url: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)
    project_id: str | None = Field(
        default=None,
        description="Optional caller-supplied id; otherwise server generates one.",
    )


class ProjectOut(BaseModel):
    project_id: str
    name: str
    repo_url: str | None
    metadata: dict[str, Any]
    created_at: str | None = None


class ReleaseRegisterRequest(BaseModel):
    project_id: str | None = None
    release_id: str | None = Field(
        default=None,
        description="Optional; server generates UUID if omitted.",
    )
    app_url: str
    commit_sha: str | None = None
    branch: str | None = None
    environment: str | None = None
    pr_url: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class ReleaseOut(BaseModel):
    release_id: str
    project_id: str | None
    app_url: str
    created_at: str | None = None
    registration: dict[str, Any] = Field(default_factory=dict)


class CheckCreateRequest(BaseModel):
    mode: Literal["full", "smoke", "journeys"] = "smoke"
    flow_ids: list[str] | None = None
    profile_name: str | None = None
    headless: bool = True
    run_mode: Literal["hybrid", "strict_steps", "goal_fallback"] = "hybrid"
    app_url: str | None = Field(
        default=None,
        description="Override deploy URL; otherwise uses registered release app_url.",
    )


class CheckCreatedOut(BaseModel):
    check_id: str
    run_id: str
    release_id: str
    status: str
    poll_url: str
    brief_url: str


class CheckStatusOut(BaseModel):
    check_id: str
    run_id: str
    release_id: str
    status: str
    decision: str | None = None
    confidence: dict[str, Any] | None = None
    passed: int = 0
    failed: int = 0
    blocker_count: int = 0
    links: dict[str, str] = Field(default_factory=dict)


class ReleaseBriefOut(ReleaseBrief):
    links: dict[str, str] = Field(default_factory=dict)
    project_id: str | None = None


class JourneyResultOut(BaseModel):
    journey_id: str
    journey_name: str = ""
    criticality: str = "other"
    status: str
    severity: str | None = None
    evidence_links: list[str] = Field(default_factory=list)


class RunSummaryOut(BaseModel):
    run_id: str
    release_id: str | None = None
    status: str
    app_url: str = ""
    decision: str | None = None
    passed: int = 0
    failed: int = 0
    journey_results: list[JourneyResultOut] = Field(default_factory=list)
    links: dict[str, str] = Field(default_factory=dict)
    raw: dict[str, Any] | None = Field(
        default=None,
        description="Full get_test_results payload for advanced clients.",
    )


class ArtifactItemOut(BaseModel):
    artifact_id: str
    run_id: str
    case_id: str | None
    artifact_type: str | None
    path: str | None
    created_at: str | None


class ArtifactListOut(BaseModel):
    items: list[ArtifactItemOut]
    next_offset: int | None = None
