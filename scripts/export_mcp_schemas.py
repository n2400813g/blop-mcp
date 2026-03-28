#!/usr/bin/env python3
"""Emit JSON Schema for MCP DTOs / envelopes into contracts/mcp/."""
from __future__ import annotations

import json
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO_ROOT / "src"))

from blop.mcp.dto import (  # noqa: E402
    CaptureArtifactResultDTO,
    JourneyListDTO,
    PerformStepResultDTO,
    PrdSummaryDTO,
    ReleaseAndJourneysDTO,
    ReleaseContextDTO,
    RunObservationResultDTO,
    UxTaxonomyDTO,
    WorkspaceContextDTO,
)
from blop.mcp.envelope import ToolError, ToolResponse  # noqa: E402
from blop.tools.atomic_browser import PerformStepSpec  # noqa: E402


def _write(name: str, model) -> None:
    out_dir = _REPO_ROOT / "contracts" / "mcp"
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"{name}.schema.json"
    schema = model.model_json_schema(mode="serialization")
    path.write_text(json.dumps(schema, indent=2) + "\n", encoding="utf-8")
    print(f"wrote {path}")


def main() -> None:
    models = [
        ("tool_error", ToolError),
        ("tool_response_generic", ToolResponse[dict]),
        ("workspace_context", WorkspaceContextDTO),
        ("release_context", ReleaseContextDTO),
        ("journey_list", JourneyListDTO),
        ("release_and_journeys", ReleaseAndJourneysDTO),
        ("prd_summary", PrdSummaryDTO),
        ("ux_taxonomy", UxTaxonomyDTO),
        ("perform_step_spec", PerformStepSpec),
        ("perform_step_result", PerformStepResultDTO),
        ("capture_artifact_result", CaptureArtifactResultDTO),
        ("run_observation_result", RunObservationResultDTO),
    ]
    for name, m in models:
        _write(name, m)


if __name__ == "__main__":
    main()
