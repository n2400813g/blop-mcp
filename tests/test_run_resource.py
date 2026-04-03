# tests/test_run_resource.py
import json

import pytest


@pytest.mark.asyncio
async def test_get_run_summary_returns_none_for_unknown(tmp_path, monkeypatch):
    """get_run_summary returns None for an unknown run_id."""
    monkeypatch.setenv("BLOP_DB_PATH", str(tmp_path / "test.db"))
    from blop.storage.sqlite import get_run_summary, init_db

    await init_db()
    result = await get_run_summary("nonexistent-run-id")
    assert result is None


@pytest.mark.asyncio
async def test_get_run_summary_returns_run_fields(tmp_path, monkeypatch):
    """get_run_summary returns run fields including release_id from release_snapshots."""
    monkeypatch.setenv("BLOP_DB_PATH", str(tmp_path / "test.db"))
    import aiosqlite

    from blop.storage.sqlite import get_run_summary, init_db

    await init_db()
    db_path = str(tmp_path / "test.db")
    run_id = "run-abc"
    release_id = "rel-xyz"

    async with aiosqlite.connect(db_path) as db:
        await db.execute(
            """INSERT INTO runs (run_id, app_url, status, flow_ids_json, run_mode, started_at)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (run_id, "https://example.com", "completed", json.dumps(["f1", "f2"]), "replay", "2026-04-02T10:00:00"),
        )
        await db.execute(
            """INSERT INTO release_snapshots (release_id, app_url, created_at, snapshot_json, brief_json, run_id)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (release_id, "https://example.com", "2026-04-02T10:00:01", "{}", "{}", run_id),
        )
        await db.commit()

    result = await get_run_summary(run_id)
    assert result is not None
    assert result["run_id"] == run_id
    assert result["status"] == "completed"
    assert result["flow_count"] == 2
    assert result["release_id"] == release_id
    assert result["app_url"] == "https://example.com"
