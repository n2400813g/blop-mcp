"""Structured errors for MCP tool boundaries and internal APIs."""

from __future__ import annotations

import sqlite3
from typing import TYPE_CHECKING, Any, Literal

if TYPE_CHECKING:
    from pydantic import ValidationError

BLOP_STORAGE_SQLITE_ERROR = "BLOP_STORAGE_SQLITE_ERROR"
BLOP_STORAGE_DB_OPEN_FAILED = "BLOP_STORAGE_DB_OPEN_FAILED"
BLOP_STORAGE_MIGRATION_FAILED = "BLOP_STORAGE_MIGRATION_FAILED"

BLOP_VALIDATION_FAILED = "BLOP_VALIDATION_FAILED"
BLOP_RUN_NOT_FOUND = "BLOP_RUN_NOT_FOUND"
BLOP_FLOW_NOT_FOUND = "BLOP_FLOW_NOT_FOUND"
BLOP_CASE_NOT_FOUND = "BLOP_CASE_NOT_FOUND"
BLOP_RELEASE_NOT_FOUND = "BLOP_RELEASE_NOT_FOUND"
BLOP_CLUSTER_NOT_FOUND = "BLOP_CLUSTER_NOT_FOUND"
BLOP_RESOURCE_NOT_FOUND = "BLOP_RESOURCE_NOT_FOUND"
BLOP_REGRESSION_START_FAILED = "BLOP_REGRESSION_START_FAILED"
BLOP_AUTH_INVALID_INPUT = "BLOP_AUTH_INVALID_INPUT"
BLOP_AUTH_PROFILE_NOT_FOUND = "BLOP_AUTH_PROFILE_NOT_FOUND"
BLOP_AUTH_CAPTURE_TIMEOUT = "BLOP_AUTH_CAPTURE_TIMEOUT"
BLOP_PM4PY_INSIGHTS_FAILED = "BLOP_PM4PY_INSIGHTS_FAILED"
BLOP_MOBILE_REPLAY_STEP_FAILED = "BLOP_MOBILE_REPLAY_STEP_FAILED"
BLOP_CAPABILITY_DISABLED = "BLOP_CAPABILITY_DISABLED"
BLOP_MCP_INTERNAL_TOOL_ERROR = "BLOP_MCP_INTERNAL_TOOL_ERROR"
BLOP_URL_VALIDATION_FAILED = "BLOP_URL_VALIDATION_FAILED"
BLOP_STORAGE_OPERATION_FAILED = "BLOP_STORAGE_OPERATION_FAILED"
BLOP_SECURITY_VALIDATION_FAILED = "BLOP_SECURITY_VALIDATION_FAILED"
BLOP_TRIAGE_INVALID_INPUT = "BLOP_TRIAGE_INVALID_INPUT"
BLOP_BROWSER_SESSION_ERROR = "BLOP_BROWSER_SESSION_ERROR"
BLOP_CODEGEN_FLOW_NOT_FOUND = "BLOP_CODEGEN_FLOW_NOT_FOUND"
BLOP_VISUAL_BASELINE_NOT_FOUND = "BLOP_VISUAL_BASELINE_NOT_FOUND"

BLOP_STAGE_VALIDATE_FAILED = "BLOP_STAGE_VALIDATE_FAILED"
BLOP_STAGE_AUTH_FAILED = "BLOP_STAGE_AUTH_FAILED"
BLOP_STAGE_EXECUTE_FAILED = "BLOP_STAGE_EXECUTE_FAILED"
BLOP_STAGE_CLASSIFY_FAILED = "BLOP_STAGE_CLASSIFY_FAILED"
BLOP_STAGE_REPORT_FAILED = "BLOP_STAGE_REPORT_FAILED"


class BlopError(Exception):
    """Typed error with stable ``BLOP_<DOMAIN>_<CODE>`` codes for clients and logs."""

    def __init__(
        self,
        code: str,
        message: str,
        *,
        details: dict[str, Any] | None = None,
        retryable: bool = False,
        likely_cause: str = "",
        suggested_fix: str = "",
        retry_safe: bool = False,
    ) -> None:
        self.code = code
        self.message = message
        self.details = details or {}
        self.retryable = retryable
        self.likely_cause = likely_cause
        self.suggested_fix = suggested_fix
        self.retry_safe = retry_safe
        super().__init__(message)

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {
            "error": {
                "code": self.code,
                "message": self.message,
                "retryable": self.retryable,
                "details": self.details,
            }
        }
        # Add diagnostic fields when set
        if self.likely_cause:
            out["error"]["likely_cause"] = self.likely_cause
        if self.suggested_fix:
            out["error"]["suggested_fix"] = self.suggested_fix
        if self.retry_safe:
            out["error"]["retry_safe"] = self.retry_safe
        return out

    def to_merged_response(self, **extra: Any) -> dict[str, Any]:
        """Flat-ish MCP payload: top-level ``error`` string plus structured ``blop_error``."""
        out: dict[str, Any] = {**extra, "error": self.message, "blop_error": self.to_dict()["error"]}
        return out


_StageName = Literal["VALIDATE", "AUTH", "EXECUTE", "CLASSIFY", "REPORT"]


class StageError(BlopError):
    """Pipeline stage failure with mandatory diagnostic context."""

    def __init__(
        self,
        stage: _StageName,
        code: str,
        message: str,
        *,
        likely_cause: str,
        suggested_fix: str,
        retry_safe: bool = False,
        details: dict[str, Any] | None = None,
    ) -> None:
        # Inject stage into details
        details = dict(details or {})
        details.setdefault("stage", stage)

        super().__init__(
            code,
            message,
            details=details,
            retryable=retry_safe,
            likely_cause=likely_cause,
            suggested_fix=suggested_fix,
            retry_safe=retry_safe,
        )
        self.stage = stage


def blop_error_from_sqlite(exc: sqlite3.Error) -> BlopError:
    """Map ``sqlite3.Error`` / aiosqlite failures to a stable storage code.

    ``aiosqlite.Error`` subclasses ``sqlite3.Error``, so a single
    ``except sqlite3.Error`` in the MCP boundary covers both drivers.
    """
    return BlopError(
        BLOP_STORAGE_SQLITE_ERROR,
        "SQLite operation failed.",
        retryable=True,
        details={"sqlite_message": str(exc), "sqlite_code": getattr(exc, "sqlite_errorcode", None)},
    )


def tool_error(
    message: str,
    code: str = BLOP_VALIDATION_FAILED,
    *,
    retryable: bool = False,
    details: dict[str, Any] | None = None,
    **extra: Any,
) -> dict[str, Any]:
    """Standard MCP tool error: string ``error`` plus structured ``blop_error``."""
    return BlopError(code, message, details=details, retryable=retryable).to_merged_response(**extra)


def wrap_validation_error(exc: "ValidationError", context: str) -> dict[str, Any]:
    """Convert a Pydantic ValidationError into a structured BLOP_VALIDATION_FAILED envelope.

    Use this around ``Model.model_validate(user_input)`` calls so the LLM receives
    field-level detail (field name + message) rather than a generic internal-error.
    """
    details_parts = []
    for e in exc.errors():
        loc = ".".join(str(p) for p in e.get("loc", [])) or "(root)"
        msg = e.get("msg", "invalid")
        details_parts.append(f"{loc}: {msg}")
    details_str = "; ".join(details_parts)
    return tool_error(
        f"Invalid input for {context}. {details_str}",
        BLOP_VALIDATION_FAILED,
        details={"context": context, "validation_errors": exc.errors()},
    )


def merge_tool_error(src: dict[str, Any], **extra: Any) -> dict[str, Any]:
    """Copy ``error`` / ``blop_error`` from a nested tool result into a new payload."""
    out: dict[str, Any] = {**extra}
    err = src.get("error")
    if isinstance(err, dict) and "code" in err:
        out["error"] = err.get("message", str(err))
        out["blop_error"] = err
    else:
        out["error"] = err if isinstance(err, str) else str(err)
        if "blop_error" in src:
            out["blop_error"] = src["blop_error"]
    return out
