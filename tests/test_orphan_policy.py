import asyncio
import json

import pytest


@pytest.mark.asyncio
async def test_cancelled_task_marks_run_interrupted(tmp_path, monkeypatch):
    """When a regression background task is cancelled, the run is marked interrupted."""
    monkeypatch.setenv("BLOP_DB_PATH", str(tmp_path / "test.db"))
    import aiosqlite

    from blop.storage.sqlite import init_db
    from blop.tools.regression import _PENDING_DB_FINALIZERS, _register_run_task

    await init_db()
    db_path = str(tmp_path / "test.db")
    run_id = "run-cancel-test"

    async with aiosqlite.connect(db_path) as db:
        await db.execute(
            "INSERT INTO runs (run_id, app_url, status, flow_ids_json, run_mode) VALUES (?, ?, ?, ?, ?)",
            (run_id, "https://example.com", "running", json.dumps(["f1"]), "replay"),
        )
        await db.commit()

    async def long_running():
        await asyncio.sleep(60)

    task = asyncio.create_task(long_running())
    _register_run_task(run_id, task)
    task.cancel()
    await asyncio.sleep(0.2)
    if _PENDING_DB_FINALIZERS:
        await asyncio.gather(*list(_PENDING_DB_FINALIZERS), return_exceptions=True)

    async with aiosqlite.connect(db_path) as db:
        async with db.execute("SELECT status FROM runs WHERE run_id = ?", (run_id,)) as cur:
            row = await cur.fetchone()
    assert row is not None
    assert row[0] == "interrupted", f"Expected 'interrupted', got '{row[0]}'"


@pytest.mark.asyncio
async def test_init_db_sweeps_stale_runs(tmp_path, monkeypatch):
    """init_db() marks runs stuck in 'running' or 'queued' as 'interrupted'."""
    db_path = str(tmp_path / "sweep_test.db")
    monkeypatch.setenv("BLOP_DB_PATH", db_path)
    import aiosqlite

    from blop.storage.sqlite import init_db

    await init_db()

    async with aiosqlite.connect(db_path) as db:
        await db.execute(
            "INSERT INTO runs (run_id, app_url, status, flow_ids_json, run_mode) VALUES (?, ?, ?, ?, ?)",
            ("stale-running", "https://example.com", "running", "[]", "replay"),
        )
        await db.execute(
            "INSERT INTO runs (run_id, app_url, status, flow_ids_json, run_mode) VALUES (?, ?, ?, ?, ?)",
            ("stale-queued", "https://example.com", "queued", "[]", "replay"),
        )
        await db.execute(
            "INSERT INTO runs (run_id, app_url, status, flow_ids_json, run_mode) VALUES (?, ?, ?, ?, ?)",
            ("already-done", "https://example.com", "completed", "[]", "replay"),
        )
        await db.commit()

    await init_db()

    async with aiosqlite.connect(db_path) as db:
        async with db.execute("SELECT run_id, status FROM runs ORDER BY run_id") as cur:
            rows = {row[0]: row[1] for row in await cur.fetchall()}

    assert rows["stale-running"] == "interrupted", f"Got {rows['stale-running']}"
    assert rows["stale-queued"] == "interrupted", f"Got {rows['stale-queued']}"
    assert rows["already-done"] == "completed"
