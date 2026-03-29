from __future__ import annotations

import os
import stat
from unittest.mock import AsyncMock, patch

import aiosqlite
import pytest

from blop.engine.errors import BlopError
from blop.storage import sqlite


class _FakeDb:
    def __init__(self, side_effect=None) -> None:
        self.execute = AsyncMock(side_effect=side_effect)
        self.commit = AsyncMock()


@pytest.mark.asyncio
async def test_migrate_does_not_advance_version_on_non_duplicate_failure():
    db = _FakeDb(side_effect=RuntimeError("disk I/O error"))
    with patch("blop.storage.sqlite._get_schema_version", new=AsyncMock(return_value=16)):
        with patch("blop.storage.sqlite._set_schema_version", new=AsyncMock()) as set_version:
            with pytest.raises(BlopError, match="Schema migration 17 failed"):
                await sqlite._migrate(db)

    set_version.assert_not_awaited()


@pytest.mark.asyncio
async def test_migrate_advances_version_on_duplicate_column_error():
    db = _FakeDb(side_effect=RuntimeError("duplicate column name: next_actions_json"))
    with patch("blop.storage.sqlite._get_schema_version", new=AsyncMock(return_value=16)):
        with patch("blop.storage.sqlite._set_schema_version", new=AsyncMock()) as set_version:
            await sqlite._migrate(db)

    # Starting at version 16 now runs migrations 17-28 (12 total),
    # all of which should still advance the version on duplicate-column errors.
    assert set_version.await_count == 12
    calls = set_version.await_args_list
    assert calls[0].args == (db, 17)
    assert calls[1].args == (db, 18)
    assert calls[2].args == (db, 19)
    assert calls[3].args == (db, 20)
    assert calls[4].args == (db, 21)
    assert calls[5].args == (db, 22)
    assert calls[6].args == (db, 23)
    assert calls[7].args == (db, 24)
    assert calls[8].args == (db, 25)
    assert calls[9].args == (db, 26)
    assert calls[10].args == (db, 27)
    assert calls[11].args == (db, 28)


@pytest.mark.asyncio
async def test_init_db_is_idempotent(tmp_path):
    """Re-running init_db() on an existing v28 DB must not raise and must not regress schema_version."""
    db_path = str(tmp_path / "smoke.db")
    with patch.dict(os.environ, {"BLOP_DB_PATH": db_path}):
        await sqlite.init_db()
        async with aiosqlite.connect(db_path) as db:
            version_after_first = await sqlite._get_schema_version(db)
        await sqlite.init_db()
        async with aiosqlite.connect(db_path) as db:
            version_after_second = await sqlite._get_schema_version(db)
    assert version_after_first == version_after_second


@pytest.mark.asyncio
async def test_fresh_install_entrypoints_are_resolvable():
    """blop-mcp and blop entrypoints must be importable from the installed package."""
    import importlib

    blop_module = importlib.util.find_spec("blop")
    assert blop_module is not None, "blop package is not installed or not resolvable"

    server_spec = importlib.util.find_spec("blop.server")
    assert server_spec is not None, "blop.server module is not resolvable — install may be broken"


def test_npm_wizard_script_has_correct_shebang():
    """setup_mobile_test_env.sh must be executable and start with a bash shebang."""
    repo_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    script = os.path.join(repo_root, "scripts", "setup_mobile_test_env.sh")
    assert os.path.isfile(script), f"Wizard script not found at {script}"
    file_mode = os.stat(script).st_mode
    assert file_mode & stat.S_IXUSR, "setup_mobile_test_env.sh is not executable"
    with open(script) as fh:
        first_line = fh.readline().strip()
    assert first_line.startswith("#!") and "bash" in first_line, f"Expected bash shebang, got: {first_line!r}"
