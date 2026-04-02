"""Standard MCP tool response envelope for new context/atomic tools."""

from __future__ import annotations

from typing import Any, Generic, TypeVar

from pydantic import BaseModel, model_validator

from blop.mcp import errors as mcp_errors

T = TypeVar("T")

_LEGACY_TOP_KEYS = frozenset({"run_id", "release_id", "flow_id", "case_id", "status", "decision"})
_ENVELOPE_KEYS = frozenset({"ok", "data", "mcp_error", "request_id", "blop_error"})


class ToolError(BaseModel):
    code: str
    message: str
    details: Any | None = None
    likely_cause: str = ""
    suggested_fix: str = ""
    retry_safe: bool = False
    stage: str | None = None

    @model_validator(mode="before")
    @classmethod
    def _legacy_detail_key(cls, data: Any) -> Any:
        if isinstance(data, dict) and "detail" in data and "details" not in data:
            data = {**data, "details": data.get("detail")}
        return data


class WorkflowHint(BaseModel):
    next_action: str
    poll_recipe: dict[str, Any] | None = None
    estimated_duration_s: tuple[int, int] | None = None
    progress_hint: str = ""


def build_poll_workflow_hint(run_id: str, flow_count: int) -> WorkflowHint:
    """Build a WorkflowHint for a queued async run."""
    if flow_count <= 0:
        min_s, max_s = 30, 300
    else:
        min_s = flow_count * 10
        max_s = flow_count * 45
    min_min = max(1, min_s // 60)
    max_min = max_s // 60 + 1
    return WorkflowHint(
        next_action=(
            f"call get_test_results(run_id='{run_id}') every 4s "
            "until status is 'completed', 'failed', 'cancelled', or 'interrupted'"
        ),
        poll_recipe={
            "tool": "get_test_results",
            "args_template": {"run_id": run_id},
            "terminal_statuses": ["completed", "failed", "cancelled", "interrupted"],
            "interval_s": 4,
            "timeout_s": 900,
        },
        estimated_duration_s=(min_s, max_s),
        progress_hint=(
            f"typically {min_min}–{max_min} min for {flow_count}-flow replay"
            if flow_count > 0
            else f"typically {min_min}–{max_min} min"
        ),
    )


class ToolResponse(BaseModel, Generic[T]):
    ok: bool
    data: T | None = None
    error: ToolError | None = None


def ok_response(data: Any) -> ToolResponse[Any]:
    return ToolResponse(ok=True, data=data, error=None)


def err_response(
    code: str,
    message: str,
    detail: str | None = None,
    *,
    details: Any | None = None,
    likely_cause: str = "",
    suggested_fix: str = "",
    retry_safe: bool = False,
    stage: str | None = None,
) -> ToolResponse[Any]:
    merged_details: Any = details if details is not None else detail
    return ToolResponse(
        ok=False,
        data=None,
        error=ToolError(
            code=code,
            message=message,
            details=merged_details,
            likely_cause=likely_cause,
            suggested_fix=suggested_fix,
            retry_safe=retry_safe,
            stage=stage,
        ),
    )


def _as_dict(err: Any) -> dict[str, Any]:
    if err is None:
        return {}
    if isinstance(err, dict):
        return err
    if hasattr(err, "model_dump"):
        return err.model_dump()
    return {"code": "error", "message": str(err), "details": None}


def finalize_tool_payload(
    raw: dict[str, Any],
    *,
    request_id: str,
    tool_name: str | None = None,
) -> dict[str, Any]:
    """Normalize any handler output to include ok/data/mcp_error plus legacy fields."""
    raw = dict(raw)
    raw.pop("_mcp_finalized", None)

    # Atomic-style envelope from context_read / atomic_browser
    if raw.get("ok") is True and "data" in raw:
        data_inner = raw["data"]
        out: dict[str, Any] = {
            "ok": True,
            "data": data_inner,
            "error": None,
            "mcp_error": None,
            "request_id": request_id,
        }
        if tool_name:
            out["tool_name"] = tool_name
        if isinstance(data_inner, dict):
            for k in _LEGACY_TOP_KEYS:
                if k in data_inner:
                    out[k] = data_inner[k]
        for k, v in raw.items():
            if k not in _ENVELOPE_KEYS | {"error"} and k not in out:
                out[k] = v
        return out

    if raw.get("ok") is False:
        terr = _as_dict(raw.get("error"))
        msg = terr.get("message") or "Request failed"
        atomic_code = str(terr.get("code") or "TOOL_ERROR")
        cat = mcp_errors.category_for_atomic_code(atomic_code)
        ed: dict[str, Any] = {}
        if tool_name:
            ed["tool"] = tool_name
        if terr.get("details") is not None:
            ed["atomic_details"] = terr.get("details")
        if terr.get("likely_cause"):
            ed["likely_cause"] = terr["likely_cause"]
        if terr.get("suggested_fix"):
            ed["suggested_fix"] = terr["suggested_fix"]
        if terr.get("retry_safe") is not None:
            ed["retry_safe"] = terr["retry_safe"]
        if terr.get("stage"):
            ed["stage"] = terr["stage"]
        mcp_err = mcp_errors.build_mcp_error(
            category=cat,
            message=msg,
            internal_code=atomic_code,
            extra_details=ed or None,
        )
        out = {
            "ok": False,
            "data": raw.get("data"),
            "error": msg,
            "mcp_error": mcp_err,
            "request_id": request_id,
        }
        if tool_name:
            out["tool_name"] = tool_name
        for k, v in raw.items():
            if k not in _ENVELOPE_KEYS | {"ok", "data", "error"} and k not in out:
                out[k] = v
        return out

    # Legacy Blop / tool_error shape
    be = raw.get("blop_error")
    if isinstance(be, dict) and be.get("code"):
        msg = raw.get("error")
        if not isinstance(msg, str):
            msg = be.get("message", str(msg))
        cat = mcp_errors.category_for_blop_code(str(be["code"]))
        details = dict(be.get("details") or {})
        details.setdefault("internal_code", be["code"])
        if be.get("retryable"):
            details.setdefault("retryable", True)
        mcp_err = {"code": cat, "message": be.get("message", msg), "details": details or None}
        out = {
            "ok": False,
            "data": None,
            "error": msg,
            "blop_error": be,
            "mcp_error": mcp_err,
            "request_id": request_id,
        }
        if tool_name:
            out["tool_name"] = tool_name
        for k, v in raw.items():
            if k not in _ENVELOPE_KEYS | {"error", "blop_error"} and k not in out:
                out[k] = v
        return out

    # Legacy success — full payload becomes data
    payload = {k: v for k, v in raw.items() if k != "request_id"}
    out = {
        "ok": True,
        "data": payload,
        "error": None,
        "mcp_error": None,
        "request_id": request_id,
    }
    if tool_name:
        out["tool_name"] = tool_name
    for k in _LEGACY_TOP_KEYS:
        if k in payload:
            out[k] = payload[k]
    return out


def finalize_resource_payload(
    body: dict[str, Any],
    *,
    request_id: str,
    resource_uri: str,
) -> dict[str, Any]:
    """Resources: envelope plus legacy top-level keys duplicated from body for existing clients."""
    base = finalize_tool_payload(
        {"ok": True, "data": body},
        request_id=request_id,
        tool_name=f"resource:{resource_uri}",
    )
    if isinstance(body, dict):
        for k, v in body.items():
            if k not in base:
                base[k] = v
    return base


def finalize_resource_error(
    *,
    request_id: str,
    resource_uri: str,
    message: str,
    blop_code: str = "BLOP_MCP_INTERNAL_TOOL_ERROR",
    details: dict[str, Any] | None = None,
) -> dict[str, Any]:
    from blop.engine.errors import BlopError

    err = BlopError(blop_code, message, details=details).to_merged_response()
    base = finalize_tool_payload(err, request_id=request_id, tool_name=f"resource:{resource_uri}")
    base.setdefault("status", "error")
    return base
