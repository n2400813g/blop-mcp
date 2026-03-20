from __future__ import annotations

import asyncio

import pytest


@pytest.mark.asyncio
async def test_cancel_run_transitions_active_run_to_cancelled(tmp_path, monkeypatch):
    from blop import server
    from blop.storage import sqlite

    db_path = str(tmp_path / "cancel_run.db")
    run_id = "run_cancel_active"
    monkeypatch.setenv("BLOP_DB_PATH", db_path)

    await sqlite.init_db()
    await sqlite.create_run(
        run_id=run_id,
        app_url="https://example.com",
        profile_name=None,
        flow_ids=[],
        headless=True,
        artifacts_dir="/tmp/artifacts",
        run_mode="hybrid",
    )
    await sqlite.update_run_status(run_id, "running")

    response = await server.cancel_run(run_id)
    run = await sqlite.get_run(run_id)

    assert response["run_id"] == run_id
    assert response["new_status"] == "cancelled"
    assert run is not None
    assert run["status"] == "cancelled"


@pytest.mark.asyncio
async def test_cancel_run_is_idempotent_for_terminal_status(tmp_path, monkeypatch):
    from blop import server
    from blop.storage import sqlite

    db_path = str(tmp_path / "cancel_run_terminal.db")
    run_id = "run_cancel_done"
    monkeypatch.setenv("BLOP_DB_PATH", db_path)

    await sqlite.init_db()
    await sqlite.create_run(
        run_id=run_id,
        app_url="https://example.com",
        profile_name=None,
        flow_ids=[],
        headless=True,
        artifacts_dir="/tmp/artifacts",
        run_mode="hybrid",
    )
    await sqlite.update_run_status(run_id, "completed")

    response = await server.cancel_run(run_id)
    run = await sqlite.get_run(run_id)

    assert response["run_id"] == run_id
    assert response["previous_status"] == "completed"
    assert "note" in response
    assert run is not None
    assert run["status"] == "completed"


@pytest.mark.asyncio
async def test_cancel_run_cancels_live_task(tmp_path, monkeypatch):
    from blop import server
    from blop.storage import sqlite
    from blop.tools import regression as regression_tools

    db_path = str(tmp_path / "cancel_run_task.db")
    run_id = "run_cancel_task"
    monkeypatch.setenv("BLOP_DB_PATH", db_path)

    await sqlite.init_db()
    await sqlite.create_run(
        run_id=run_id,
        app_url="https://example.com",
        profile_name=None,
        flow_ids=[],
        headless=True,
        artifacts_dir="/tmp/artifacts",
        run_mode="hybrid",
    )
    await sqlite.update_run_status(run_id, "running")

    task = asyncio.create_task(asyncio.sleep(60))
    regression_tools._RUN_TASKS[run_id] = task

    response = await server.cancel_run(run_id)
    await asyncio.sleep(0)
    try:
        await task
    except asyncio.CancelledError:
        pass

    assert response["run_id"] == run_id
    assert response["new_status"] == "cancelled"
    assert response["task_cancelled"] is True
    assert task.cancelled()


@pytest.mark.asyncio
async def test_shutdown_run_tasks_cancels_running_tasks_and_marks_terminal(tmp_path, monkeypatch):
    from blop.storage import sqlite
    from blop.tools import regression as regression_tools

    db_path = str(tmp_path / "shutdown_tasks.db")
    run_id = "run_shutdown_task"
    monkeypatch.setenv("BLOP_DB_PATH", db_path)

    await sqlite.init_db()
    await sqlite.create_run(
        run_id=run_id,
        app_url="https://example.com",
        profile_name=None,
        flow_ids=[],
        headless=True,
        artifacts_dir="/tmp/artifacts",
        run_mode="hybrid",
    )
    await sqlite.update_run_status(run_id, "running")

    task = asyncio.create_task(asyncio.sleep(60))
    regression_tools._RUN_TASKS[run_id] = task

    stats = await regression_tools.shutdown_run_tasks(timeout_secs=0.5)
    run = await sqlite.get_run(run_id)

    assert stats["cancelled"] >= 1
    assert run is not None
    assert run["status"] == "cancelled"
    assert run_id not in regression_tools._RUN_TASKS
