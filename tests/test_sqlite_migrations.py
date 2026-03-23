from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

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
            with pytest.raises(RuntimeError, match="Migration 17 failed"):
                await sqlite._migrate(db)

    set_version.assert_not_awaited()


@pytest.mark.asyncio
async def test_migrate_advances_version_on_duplicate_column_error():
    db = _FakeDb(side_effect=RuntimeError("duplicate column name: next_actions_json"))
    with patch("blop.storage.sqlite._get_schema_version", new=AsyncMock(return_value=16)):
        with patch("blop.storage.sqlite._set_schema_version", new=AsyncMock()) as set_version:
            await sqlite._migrate(db)

    # Starting at version 16 now runs migrations 17, 18, 19, 20, and 21,
    # all of which should still advance the version on duplicate-column errors.
    assert set_version.await_count == 5
    calls = set_version.await_args_list
    assert calls[0].args == (db, 17)
    assert calls[1].args == (db, 18)
    assert calls[2].args == (db, 19)
    assert calls[3].args == (db, 20)
    assert calls[4].args == (db, 21)
