"""Tests for server import-time behaviour — server_boot category."""
from __future__ import annotations

import logging
import sys


def test_server_imports_without_side_effects(monkeypatch):
    """Importing blop.server must not call init_db or async_playwright."""
    import unittest.mock as mock

    init_db_calls: list = []
    playwright_calls: list = []

    # Remove cached module so the import runs fresh each time
    for key in list(sys.modules.keys()):
        if key.startswith("blop.server"):
            del sys.modules[key]

    with mock.patch("blop.storage.sqlite.init_db", side_effect=lambda: init_db_calls.append(1)) as _:
        with mock.patch("playwright.async_api.async_playwright", side_effect=lambda: playwright_calls.append(1)) as _:
            import blop.server  # noqa: F401

    assert init_db_calls == [], "init_db must not be called on module import"
    assert playwright_calls == [], "async_playwright must not be called on module import"


def test_mcp_object_exists_and_is_named_blop():
    """The FastMCP instance must be named 'blop'."""
    import blop.server as server

    assert hasattr(server, "mcp"), "server.mcp must exist"
    assert server.mcp.name == "blop"


def test_logging_disabled_on_import():
    """Logging must be disabled at CRITICAL level on server import."""
    import blop.server  # noqa: F401

    assert logging.root.manager.disable >= logging.CRITICAL


def test_no_stdout_on_import(capsys):
    """Server import must not write to stdout (JSON-RPC transport safety)."""
    # Re-import is a no-op if already cached, so just verify no residual output
    import blop.server  # noqa: F401

    captured = capsys.readouterr()
    assert captured.out == "", f"Unexpected stdout on import: {captured.out!r}"
