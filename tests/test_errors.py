"""Tests for blop.engine.errors."""

from __future__ import annotations

import sqlite3

from blop.engine.errors import BlopError, blop_error_from_sqlite, merge_tool_error, tool_error


def test_blop_error_to_dict_shape():
    e = BlopError("BLOP_LLM_QUOTA_EXCEEDED", "Quota hit", retryable=True, details={"provider": "google"})
    d = e.to_dict()
    assert d["error"]["code"] == "BLOP_LLM_QUOTA_EXCEEDED"
    assert d["error"]["message"] == "Quota hit"
    assert d["error"]["retryable"] is True
    assert d["error"]["details"]["provider"] == "google"


def test_to_merged_response():
    e = BlopError("BLOP_RUN_CONCURRENCY_EXCEEDED", "Too many runs", retryable=True)
    m = e.to_merged_response(run_id=None, status="error")
    assert m["error"] == "Too many runs"
    assert m["blop_error"]["code"] == "BLOP_RUN_CONCURRENCY_EXCEEDED"
    assert m["status"] == "error"


def test_merge_tool_error_propagates_blop_error():
    inner = tool_error("inner", "BLOP_RUN_NOT_FOUND", details={"run_id": "r1"})
    merged = merge_tool_error(inner, release_id="rel1")
    assert merged["error"] == "inner"
    assert merged["release_id"] == "rel1"
    assert merged["blop_error"]["code"] == "BLOP_RUN_NOT_FOUND"


def test_blop_error_from_sqlite():
    inner = sqlite3.OperationalError("disk I/O error")
    b = blop_error_from_sqlite(inner)
    assert b.code == "BLOP_STORAGE_SQLITE_ERROR"
    assert b.retryable is True
    assert "sqlite_message" in b.details
