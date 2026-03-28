"""Atomic browser MCP tools (default surface): shared Playwright session."""

from __future__ import annotations

import json
import shutil
import time
from pathlib import Path
from typing import Any, Literal, Optional

from pydantic import BaseModel, field_validator

from blop.engine.browser_session_manager import SESSION_MANAGER
from blop.mcp.dto import CaptureArtifactResultDTO, PerformStepResultDTO
from blop.mcp.envelope import err_response, ok_response
from blop.schemas import RecordedFlow
from blop.storage import files as file_store
from blop.storage import sqlite


class PerformStepSpec(BaseModel):
    """Structured single-step command for perform_step."""

    action: Literal["click", "type", "wait", "press_key", "navigate"]
    ref: str | None = None
    selector: str | None = None
    url: str | None = None
    text: str | None = None
    submit: bool = False
    slowly: bool = False
    time_secs: float | None = None
    wait_text: str | None = None
    text_gone: str | None = None
    key: str | None = None
    double_click: bool = False

    @field_validator("action", mode="before")
    @classmethod
    def _strip_action(cls, v: Any) -> Any:
        return v.strip().lower() if isinstance(v, str) else v


def _resolve_journey_entry_url(flow: RecordedFlow) -> str | None:
    if flow.entry_url:
        return flow.entry_url
    for s in sorted(flow.steps, key=lambda x: x.step_id):
        if s.action == "navigate":
            cand = (s.value or s.selector or "").strip()
            if cand.startswith("http://") or cand.startswith("https://"):
                return cand
    return flow.app_url or None


async def navigate_to_url(url: str, profile_name: Optional[str] = None) -> dict:
    try:
        raw = await SESSION_MANAGER.navigate(url, profile_name=profile_name)
        return ok_response(raw).model_dump()
    except Exception as e:
        return err_response("navigate_failed", str(e)).model_dump()


async def get_page_snapshot(selector: Optional[str] = None, filename: Optional[str] = None) -> dict:
    try:
        raw = await SESSION_MANAGER.snapshot(selector=selector, filename=filename)
        return ok_response(raw).model_dump()
    except Exception as e:
        return err_response("snapshot_failed", str(e)).model_dump()


async def perform_step(step_spec: dict) -> dict:
    try:
        spec = PerformStepSpec.model_validate(step_spec)
    except Exception as e:
        return err_response("invalid_step_spec", str(e)).model_dump()

    try:
        if spec.action == "navigate":
            if not spec.url:
                return err_response("invalid_step_spec", "navigate requires url").model_dump()
            raw = await SESSION_MANAGER.navigate(spec.url, profile_name=None)
            out = PerformStepResultDTO(action=spec.action, status="ok", detail=raw)
            return ok_response(out.model_dump()).model_dump()

        if spec.action == "click":
            raw = await SESSION_MANAGER.click(
                ref=spec.ref,
                selector=spec.selector,
                double_click=spec.double_click,
            )
            out = PerformStepResultDTO(action=spec.action, status="ok", detail=raw)
            return ok_response(out.model_dump()).model_dump()

        if spec.action == "type":
            if spec.text is None:
                return err_response("invalid_step_spec", "type requires text").model_dump()
            raw = await SESSION_MANAGER.type_text(
                ref=spec.ref,
                selector=spec.selector,
                text=spec.text,
                submit=spec.submit,
                slowly=spec.slowly,
            )
            out = PerformStepResultDTO(action=spec.action, status="ok", detail=raw)
            return ok_response(out.model_dump()).model_dump()

        if spec.action == "wait":
            raw = await SESSION_MANAGER.wait_for(
                time_secs=spec.time_secs,
                text=spec.wait_text,
                text_gone=spec.text_gone,
            )
            out = PerformStepResultDTO(action=spec.action, status="ok", detail=raw)
            return ok_response(out.model_dump()).model_dump()

        if spec.action == "press_key":
            if not spec.key:
                return err_response("invalid_step_spec", "press_key requires key").model_dump()
            raw = await SESSION_MANAGER.press_key(spec.key)
            out = PerformStepResultDTO(action=spec.action, status="ok", detail=raw)
            return ok_response(out.model_dump()).model_dump()

        return err_response("unsupported_action", spec.action).model_dump()
    except Exception as e:
        return err_response("step_failed", str(e)).model_dump()


async def capture_artifact(
    kind: str,
    metadata: Optional[dict[str, Any]] = None,
) -> dict:
    metadata = metadata or {}
    kind_l = kind.strip().lower()
    run_id = metadata.get("run_id")
    selector = metadata.get("selector")
    include_static = bool(metadata.get("include_static", False))

    try:
        if kind_l == "screenshot":
            fn = metadata.get("filename") or f"capture-{int(time.time() * 1000)}.png"
            raw = await SESSION_MANAGER.take_screenshot(
                filename=str(fn),
                full_page=bool(metadata.get("full_page", False)),
                ref=metadata.get("ref"),
                selector=selector,
                img_type=str(metadata.get("format", "png")),
            )
            path_out: str | None = raw.get("path")
            if run_id and path_out:
                file_store.ensure_run_dirs(run_id)
                case_id = str(metadata.get("case_id") or "agent")
                step = int(metadata.get("step_index", 0))
                dest = file_store.screenshot_path(run_id, case_id, step)
                shutil.copy2(path_out, dest)
                path_out = dest
            dto = CaptureArtifactResultDTO(kind="screenshot", path=path_out, run_id=run_id)
            return ok_response(dto.model_dump()).model_dump()

        if kind_l == "dom_snapshot":
            snap = await SESSION_MANAGER.snapshot(selector=selector, filename=None)
            md = snap.get("snapshot") or ""
            path_out: str | None = None
            if run_id:
                file_store.ensure_run_dirs(run_id)
                base = Path(file_store.artifacts_dir(run_id))
                base.mkdir(parents=True, exist_ok=True)
                path_out = str(base / f"dom_snapshot_{int(time.time() * 1000)}.md")
                Path(path_out).write_text(md or "", encoding="utf-8")
            else:
                raw = await SESSION_MANAGER.snapshot(
                    selector=selector,
                    filename=f"dom-{int(time.time() * 1000)}.md",
                )
                path_out = raw.get("path")
            dto = CaptureArtifactResultDTO(kind="dom_snapshot", path=path_out, run_id=run_id)
            return ok_response({**dto.model_dump(), "snapshot_excerpt": (md or "")[:2000]}).model_dump()

        if kind_l == "network_log":
            raw = await SESSION_MANAGER.network_requests(include_static=include_static)
            path_out: str | None = None
            if run_id:
                file_store.ensure_run_dirs(run_id)
                case = str(metadata.get("case_id") or "agent")
                net_dir = Path(file_store.network_log_path(run_id, case)).parent
                net_dir.mkdir(parents=True, exist_ok=True)
                path_out = str(net_dir / f"capture_{int(time.time() * 1000)}.json")
                Path(path_out).write_text(json.dumps(raw, indent=2), encoding="utf-8")
            dto = CaptureArtifactResultDTO(kind="network_log", path=path_out, run_id=run_id)
            return ok_response({**dto.model_dump(), "summary": {"count": raw.get("count", 0)}}).model_dump()

        return err_response("unsupported_kind", f"Unknown kind {kind!r}").model_dump()
    except Exception as e:
        return err_response("capture_failed", str(e)).model_dump()


async def navigate_to_journey(journey_id: str, profile_name: Optional[str] = None) -> dict:
    flow = await sqlite.get_flow(journey_id)
    if not flow:
        return err_response("not_found", f"No recorded journey/flow for id={journey_id}").model_dump()
    url = _resolve_journey_entry_url(flow)
    if not url:
        return err_response("no_entry_url", "Could not resolve entry URL from recording").model_dump()
    try:
        raw = await SESSION_MANAGER.navigate(url, profile_name=profile_name)
        return ok_response({"url": raw.get("url"), "title": raw.get("title"), "flow_id": journey_id}).model_dump()
    except Exception as e:
        return err_response("navigate_failed", str(e)).model_dump()


async def record_run_observation(
    run_id: str,
    observation_key: str,
    observation_payload: dict[str, Any],
) -> dict:
    run = await sqlite.get_run(run_id)
    if not run:
        return err_response("not_found", f"Run {run_id} not found").model_dump()
    if not observation_key.strip():
        return err_response("invalid_argument", "observation_key must be non-empty").model_dump()

    await sqlite.upsert_run_observation(run_id, observation_key.strip(), observation_payload)
    return ok_response({"run_id": run_id, "observation_key": observation_key.strip(), "updated": True}).model_dump()
