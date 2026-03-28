"""Standard MCP tool response envelope for new context/atomic tools."""

from __future__ import annotations

from typing import Any, Generic, TypeVar

from pydantic import BaseModel

T = TypeVar("T")


class ToolError(BaseModel):
    code: str
    message: str
    detail: str | None = None


class ToolResponse(BaseModel, Generic[T]):
    ok: bool
    data: T | None = None
    error: ToolError | None = None


def ok_response(data: Any) -> ToolResponse[Any]:
    return ToolResponse(ok=True, data=data, error=None)


def err_response(code: str, message: str, detail: str | None = None) -> ToolResponse[Any]:
    return ToolResponse(ok=False, data=None, error=ToolError(code=code, message=message, detail=detail))
