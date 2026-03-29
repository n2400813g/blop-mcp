from __future__ import annotations

import uuid
from importlib import import_module
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request

from blop import config
from blop.api.v1.deps import require_v1_api_key
from blop.api.v1.rate_limit import LLM_HEAVY_ROUTE_LIMIT, http_limiter
from blop.api.v1.schemas import (
    ArtifactItemOut,
    ArtifactListOut,
    CheckCreatedOut,
    CheckCreateRequest,
    CheckStatusOut,
    JourneyResultOut,
    ProjectCreate,
    ProjectOut,
    ReleaseBriefOut,
    ReleaseOut,
    ReleaseRegisterRequest,
    RunSummaryOut,
)
from blop.schemas import ReleaseBrief, ReleaseSnapshot
from blop.storage import sqlite

router = APIRouter(dependencies=[Depends(require_v1_api_key)])


async def run_release_check(*args, **kwargs):
    """Lazy wrapper to avoid importing the release-check stack at router import time."""
    return await import_module("blop.tools.release_check").run_release_check(*args, **kwargs)


async def get_test_results(*args, **kwargs):
    """Lazy wrapper to avoid importing the results stack at router import time."""
    return await import_module("blop.tools.results").get_test_results(*args, **kwargs)


def _public_base(request: Request) -> str:
    if config.BLOP_HTTP_PUBLIC_BASE_URL:
        return config.BLOP_HTTP_PUBLIC_BASE_URL
    return str(request.base_url).rstrip("/")


def _run_links(base: str, run_id: str, release_id: str | None) -> dict[str, str]:
    out = {
        "run": f"{base}/v1/runs/{run_id}",
        "artifacts": f"{base}/v1/runs/{run_id}/artifacts",
        "stream": f"{base}/runs/{run_id}/stream",
    }
    if release_id:
        out["brief"] = f"{base}/v1/releases/{release_id}/brief"
    return out


def _criticality_for_mode(mode: str) -> list[str]:
    if mode == "full":
        return ["revenue", "activation", "retention", "support", "other"]
    if mode == "smoke":
        return ["revenue", "activation"]
    return ["revenue", "activation"]


@router.post("/projects", response_model=ProjectOut)
async def create_project(body: ProjectCreate) -> ProjectOut:
    pid = body.project_id or uuid.uuid4().hex
    await sqlite.save_project(pid, body.name, body.repo_url, body.metadata)
    row = await sqlite.get_project(pid)
    if not row:
        raise HTTPException(status_code=500, detail="Failed to persist project")
    return ProjectOut(
        project_id=row["project_id"],
        name=row["name"],
        repo_url=row["repo_url"],
        metadata=row["metadata"],
        created_at=row.get("created_at"),
    )


@router.post("/releases", response_model=ReleaseOut)
async def register_release(body: ReleaseRegisterRequest) -> ReleaseOut:
    if body.project_id:
        proj = await sqlite.get_project(body.project_id)
        if not proj:
            raise HTTPException(status_code=404, detail=f"Unknown project_id={body.project_id!r}")
    rid = body.release_id or uuid.uuid4().hex
    reg_meta: dict[str, Any] = {
        **body.metadata,
        "commit_sha": body.commit_sha,
        "branch": body.branch,
        "environment": body.environment,
        "pr_url": body.pr_url,
    }
    reg_meta = {k: v for k, v in reg_meta.items() if v is not None}
    await sqlite.upsert_release_registration(
        release_id=rid,
        app_url=body.app_url.strip(),
        project_id=body.project_id,
        registration_metadata=reg_meta,
    )
    row = await sqlite.get_release_row(rid)
    if not row:
        raise HTTPException(status_code=500, detail="Failed to persist release")
    snap: dict[str, Any] = {}
    try:
        s = ReleaseSnapshot.model_validate_json(row["snapshot_json"])
        snap = s.metadata or {}
    except Exception:
        pass
    return ReleaseOut(
        release_id=rid,
        project_id=row.get("project_id"),
        app_url=row["app_url"],
        created_at=row.get("created_at"),
        registration=snap,
    )


@router.post("/releases/{release_id}/checks", response_model=CheckCreatedOut)
@http_limiter.limit(LLM_HEAVY_ROUTE_LIMIT)
async def create_check(
    release_id: str,
    body: CheckCreateRequest,
    request: Request,
) -> CheckCreatedOut:
    row = await sqlite.get_release_row(release_id)
    if not row:
        raise HTTPException(status_code=404, detail=f"Unknown release_id={release_id!r}")
    app_url = (body.app_url or row["app_url"] or "").strip()
    if not app_url:
        raise HTTPException(status_code=400, detail="app_url required (register release or pass in body)")

    if body.mode == "journeys":
        if not body.flow_ids:
            raise HTTPException(
                status_code=422,
                detail="mode=journeys requires flow_ids",
            )
        flow_ids = body.flow_ids
        criticality_filter = _criticality_for_mode("full")
    else:
        flow_ids = None
        criticality_filter = _criticality_for_mode(body.mode)

    result = await run_release_check(
        app_url=app_url,
        flow_ids=flow_ids,
        profile_name=body.profile_name,
        mode="replay",
        criticality_filter=criticality_filter,
        release_id=release_id,
        headless=body.headless,
        run_mode=body.run_mode,
    )
    if "error" in result:
        raise HTTPException(status_code=400, detail=str(result["error"]))

    run_id = result.get("run_id") or ""
    if not run_id:
        raise HTTPException(status_code=500, detail="No run_id returned from release check")

    base = _public_base(request)
    return CheckCreatedOut(
        check_id=run_id,
        run_id=run_id,
        release_id=release_id,
        status=result.get("status", "queued"),
        poll_url=f"{base}/v1/releases/{release_id}/checks/{run_id}",
        brief_url=f"{base}/v1/releases/{release_id}/brief",
    )


@router.get("/releases/{release_id}/checks/{check_id}", response_model=CheckStatusOut)
async def get_check_status(
    release_id: str,
    check_id: str,
    request: Request,
) -> CheckStatusOut:
    link = await sqlite.get_release_id_for_run(check_id)
    if not link or link["release_id"] != release_id:
        raise HTTPException(status_code=404, detail="Check not found for this release")

    run = await sqlite.get_run(check_id)
    if not run:
        raise HTTPException(status_code=404, detail="Run not found")

    brief_raw = await sqlite.get_release_brief(release_id)
    decision = None
    confidence = None
    blocker_count = 0
    passed = 0
    failed = 0
    if brief_raw and brief_raw.get("run_id") == check_id:
        decision = brief_raw.get("decision")
        confidence = brief_raw.get("confidence")
        blocker_count = int(brief_raw.get("blocker_count") or 0)

    for c in run.get("cases") or []:
        st = c.get("status") if isinstance(c, dict) else getattr(c, "status", "")
        if st == "pass":
            passed += 1
        elif st in ("fail", "error", "blocked"):
            failed += 1

    if passed == 0 and failed == 0:
        cases = await sqlite.list_cases_for_run(check_id)
        for c in cases:
            if c.status == "pass":
                passed += 1
            elif c.status in ("fail", "error", "blocked"):
                failed += 1

    base = _public_base(request)
    return CheckStatusOut(
        check_id=check_id,
        run_id=check_id,
        release_id=release_id,
        status=run.get("status", "unknown"),
        decision=decision,
        confidence=confidence,
        passed=passed,
        failed=failed,
        blocker_count=blocker_count,
        links=_run_links(base, check_id, release_id),
    )


@router.get("/releases/{release_id}/brief", response_model=ReleaseBriefOut)
async def get_release_brief_http(release_id: str, request: Request) -> ReleaseBriefOut:
    raw = await sqlite.get_release_brief(release_id)
    if not raw:
        raise HTTPException(
            status_code=404,
            detail="No release brief yet — register the release and run a check first.",
        )
    brief = ReleaseBrief.model_validate(raw)
    row = await sqlite.get_release_row(release_id)
    base = _public_base(request)
    rid = brief.run_id
    links = {
        "brief": f"{base}/v1/releases/{release_id}/brief",
        "run": f"{base}/v1/runs/{rid}" if rid else "",
        "artifacts": f"{base}/v1/runs/{rid}/artifacts" if rid else "",
        "stream": f"{base}/runs/{rid}/stream" if rid else "",
        "mcp_brief": f"blop://release/{release_id}/brief",
    }
    links = {k: v for k, v in links.items() if v}
    payload = brief.model_dump()
    payload["links"] = links
    payload["project_id"] = row.get("project_id") if row else None
    return ReleaseBriefOut.model_validate(payload)


@router.get("/runs/{run_id}", response_model=RunSummaryOut)
async def get_run_detail(run_id: str, request: Request, include_raw: bool = False) -> RunSummaryOut:
    report = await get_test_results(run_id)
    if "error" in report:
        raise HTTPException(status_code=404, detail=report["error"])

    link = await sqlite.get_release_id_for_run(run_id)
    release_id = link["release_id"] if link else None

    rec = report.get("release_recommendation") or {}
    decision = rec.get("decision")

    passed = 0
    failed = 0
    journey_results: list[JourneyResultOut] = []
    for c in report.get("cases") or []:
        if not isinstance(c, dict):
            continue
        st = c.get("status", "")
        if st == "pass":
            passed += 1
        elif st in ("fail", "error", "blocked"):
            failed += 1
        jid = c.get("flow_id") or c.get("case_id") or ""
        pics = c.get("screenshots") or []
        ev: list[str] = []
        if isinstance(pics, list):
            ev = [str(p) for p in pics[:5]]
        journey_results.append(
            JourneyResultOut(
                journey_id=str(jid),
                journey_name=str(c.get("flow_name") or ""),
                criticality=str(c.get("business_criticality") or "other"),
                status=st,
                severity=c.get("severity"),
                evidence_links=ev,
            )
        )

    base = _public_base(request)
    return RunSummaryOut(
        run_id=run_id,
        release_id=release_id,
        status=report.get("status", "unknown"),
        app_url=report.get("run_environment", {}).get("app_url", "") or report.get("app_url", ""),
        decision=decision,
        passed=passed,
        failed=failed,
        journey_results=journey_results,
        links=_run_links(base, run_id, release_id),
        raw=report if include_raw else None,
    )


@router.get("/runs/{run_id}/artifacts", response_model=ArtifactListOut)
async def list_run_artifacts(
    run_id: str,
    offset: int = 0,
    limit: int = 50,
) -> ArtifactListOut:
    run = await sqlite.get_run(run_id)
    if not run:
        raise HTTPException(status_code=404, detail="Run not found")
    safe_limit = max(1, min(limit, 200))
    safe_offset = max(0, offset)
    all_a = await sqlite.list_artifacts_for_run(run_id)
    page = all_a[safe_offset : safe_offset + safe_limit]
    next_off = safe_offset + safe_limit if safe_offset + safe_limit < len(all_a) else None
    return ArtifactListOut(
        items=[ArtifactItemOut(**a) for a in page],
        next_offset=next_off,
    )


@router.post("/webhooks/test-results")
async def webhook_test_results_placeholder() -> dict[str, str]:
    raise HTTPException(
        status_code=501,
        detail="Outbound webhooks are not implemented in this v1 build.",
    )
