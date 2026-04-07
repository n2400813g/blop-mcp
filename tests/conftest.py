from __future__ import annotations

import asyncio
import os

import pytest


@pytest.fixture(autouse=True)
def reset_module_state_between_tests():
    """Keep module-level mutable state from leaking across tests."""
    try:
        from blop.engine import auth as auth_engine

        auth_engine._auth_cache.clear()
        auth_engine._validated_session_cache.clear()
        auth_engine._login_locks.clear()
    except Exception:
        pass

    try:
        from blop.engine import secrets as secrets_engine

        secrets_engine._secrets_cache = None
    except Exception:
        pass

    try:
        from blop.tools import network as network_tools

        network_tools._active_routes.clear()
    except Exception:
        pass

    try:
        from blop.tools import regression as regression_tools

        for task in list(regression_tools._RUN_TASKS.values()):
            task.cancel()
        regression_tools._RUN_TASKS.clear()
    except Exception:
        pass

    yield

    try:
        from blop.engine import auth as auth_engine

        auth_engine._auth_cache.clear()
        auth_engine._validated_session_cache.clear()
        auth_engine._login_locks.clear()
    except Exception:
        pass

    try:
        from blop.engine import secrets as secrets_engine

        secrets_engine._secrets_cache = None
    except Exception:
        pass

    try:
        from blop.tools import network as network_tools

        network_tools._active_routes.clear()
    except Exception:
        pass

    try:
        from blop.tools import regression as regression_tools

        for task in list(regression_tools._RUN_TASKS.values()):
            task.cancel()
        regression_tools._RUN_TASKS.clear()
    except Exception:
        pass


@pytest.fixture
def tmp_db(tmp_path):
    """Isolated SQLite DB fixture.

    Sets BLOP_DB_PATH to a temp file, initialises the schema, and restores
    the original value on teardown. Synchronous — uses asyncio.run for init.
    """
    from blop.storage import sqlite as _sqlite_mod
    from blop.storage.sqlite import init_db

    db_file = tmp_path / "test_blop.db"
    original = os.environ.get("BLOP_DB_PATH")
    os.environ["BLOP_DB_PATH"] = str(db_file)

    # Force-reset the shared connection so init_db opens a fresh connection
    # to the temp DB (avoids cross-loop connection reuse between fixtures).
    _sqlite_mod._shared_conn = None
    _sqlite_mod._conn_path = None

    loop = asyncio.new_event_loop()
    loop.run_until_complete(init_db())
    loop.close()

    yield str(db_file)

    # Teardown: forget the temp-DB connection so subsequent tests don't
    # try to reuse a connection that was created in a now-closed event loop.
    _sqlite_mod._shared_conn = None
    _sqlite_mod._conn_path = None

    # Restore original env var
    if original is None:
        os.environ.pop("BLOP_DB_PATH", None)
    else:
        os.environ["BLOP_DB_PATH"] = original
