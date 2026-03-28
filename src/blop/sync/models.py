"""Data models for blop-mcp → hosted blop sync payloads."""

from dataclasses import dataclass, field
from typing import Any


@dataclass
class RunCasePayload:
    case_id_external: str
    status: str  # pass | fail | error | skip
    flow_id_external: str | None = None
    severity: str | None = None
    result_json: dict[str, Any] | None = None
    step_failure_index: int | None = None
    assertion_failures: list[Any] | None = None


@dataclass
class SyncRunPayload:
    blop_mcp_run_id: str
    project_id: str
    url: str
    run_cases: list[RunCasePayload] = field(default_factory=list)
    release_name: str | None = None
    release_version: str | None = None
    environment: str = "production"
    blop_mcp_release_id: str | None = None
