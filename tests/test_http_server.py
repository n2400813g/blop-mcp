"""Tests for blop.server_http (HTTP SSE server + /v1 API)."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

# Skip entire module if FastAPI/server deps not installed
pytest.importorskip("fastapi")

try:
    from blop.server_http import app
except (ImportError, AttributeError):
    pytest.skip("blop[server] not installed (fastapi, sse-starlette, uvicorn)", allow_module_level=True)


def _v1_client():
    from httpx import ASGITransport, AsyncClient

    transport = ASGITransport(app=app)
    return AsyncClient(transport=transport, base_url="http://test")


@pytest.mark.asyncio
async def test_health_endpoint():
    """GET /health returns 200 with {status: ok}."""
    from httpx import ASGITransport, AsyncClient

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


@pytest.mark.asyncio
async def test_stream_run_completed():
    """Stream endpoint yields SSE events and terminal when run is completed."""
    from httpx import ASGITransport, AsyncClient

    completed_run = {
        "run_id": "run_abc",
        "status": "completed",
        "app_url": "https://example.com",
        "started_at": "2026-03-19T10:00:00Z",
        "completed_at": "2026-03-19T10:05:00Z",
    }
    health_events = [
        {"event_id": "evt_1", "event_type": "case_started", "payload": {"flow_id": "f1", "case_id": "c1"}},
        {"event_id": "evt_2", "event_type": "case_completed", "payload": {"flow_id": "f1", "status": "pass"}},
    ]

    with patch("blop.storage.sqlite.get_run", new=AsyncMock(return_value=completed_run)):
        with patch(
            "blop.storage.sqlite.list_run_health_events",
            new=AsyncMock(return_value=health_events),
        ):
            transport = ASGITransport(app=app)
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                response = await client.get("/runs/run_abc/stream")

    assert response.status_code == 200
    assert "text/event-stream" in response.headers.get("content-type", "")

    # Consume SSE stream and collect data payloads
    data_payloads: list[str] = []
    async for line in response.aiter_lines():
        if line.startswith("data:"):
            data_payloads.append(line[5:].strip())
        if len(data_payloads) >= 3:
            break

    # Should have: case_started payload, case_completed payload, and terminal "completed"
    assert len(data_payloads) >= 2
    assert "completed" in data_payloads[-1]


@pytest.mark.asyncio
async def test_stream_run_empty_events_then_terminal():
    """Stream with no health events but completed run yields terminal event."""
    from httpx import ASGITransport, AsyncClient

    completed_run = {"run_id": "run_xyz", "status": "completed"}
    with patch("blop.storage.sqlite.get_run", new=AsyncMock(return_value=completed_run)):
        with patch(
            "blop.storage.sqlite.list_run_health_events",
            new=AsyncMock(return_value=[]),
        ):
            transport = ASGITransport(app=app)
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                response = await client.get("/runs/run_xyz/stream")

    assert response.status_code == 200
    assert "text/event-stream" in response.headers.get("content-type", "")

    # Consume stream; should get terminal event quickly (run is already completed)
    chunks: list[str] = []
    async for line in response.aiter_lines():
        chunks.append(line)
        if line.startswith("data:") and "completed" in line:
            break
        if len(chunks) > 20:
            break

    assert any("completed" in line for line in chunks)


@pytest.mark.asyncio
async def test_v1_post_projects(tmp_db):
    """POST /v1/projects creates a project row."""
    from blop import config

    with patch.object(config, "BLOP_HTTP_API_KEY", None):
        async with _v1_client() as client:
            r = await client.post(
                "/v1/projects",
                json={"name": "acme", "repo_url": "https://github.com/acme/app"},
            )
        assert r.status_code == 200
        data = r.json()
        assert data["name"] == "acme"
        assert data["project_id"]
        assert data["repo_url"] == "https://github.com/acme/app"


@pytest.mark.asyncio
async def test_v1_api_key_required_when_set(tmp_db):
    from blop import config

    with patch.object(config, "BLOP_HTTP_API_KEY", "test-secret-key"):
        async with _v1_client() as client:
            r = await client.post("/v1/projects", json={"name": "x"})
        assert r.status_code == 401

        async with _v1_client() as client:
            r2 = await client.post(
                "/v1/projects",
                json={"name": "x"},
                headers={"Authorization": "Bearer test-secret-key"},
            )
        assert r2.status_code == 200


@pytest.mark.asyncio
async def test_v1_release_brief(tmp_db):
    from blop import config
    from blop.storage.sqlite import save_release_brief

    await save_release_brief(
        "rel-1",
        "run-z",
        "https://app.example.com",
        {
            "release_id": "rel-1",
            "run_id": "run-z",
            "app_url": "https://app.example.com",
            "created_at": "2026-01-01T00:00:00+00:00",
            "decision": "SHIP",
            "risk": {"value": 10, "level": "low"},
            "confidence": {"value": 0.9, "label": "high"},
            "blocker_count": 0,
            "blocker_journey_names": [],
            "critical_journey_failures": 0,
            "top_actions": [],
        },
    )
    with patch.object(config, "BLOP_HTTP_API_KEY", None):
        async with _v1_client() as client:
            r = await client.get("/v1/releases/rel-1/brief")
    assert r.status_code == 200
    body = r.json()
    assert body["decision"] == "SHIP"
    assert "links" in body
    assert "/v1/runs/run-z" in body["links"]["run"]


@pytest.mark.asyncio
async def test_v1_post_checks_mocked(tmp_db):
    from blop import config

    with patch.object(config, "BLOP_HTTP_API_KEY", None):
        from blop.storage.sqlite import upsert_release_registration

        await upsert_release_registration(
            release_id="rel-x",
            app_url="https://deploy.example.com",
            project_id=None,
            registration_metadata={"branch": "main"},
        )
        mock_result = {
            "run_id": "run-mock",
            "status": "queued",
            "release_id": "rel-x",
        }
        with patch(
            "blop.api.v1.router.run_release_check",
            new=AsyncMock(return_value=mock_result),
        ):
            async with _v1_client() as client:
                r = await client.post(
                    "/v1/releases/rel-x/checks",
                    json={"mode": "smoke"},
                )
        assert r.status_code == 200
        out = r.json()
        assert out["run_id"] == "run-mock"
        assert out["check_id"] == "run-mock"


@pytest.mark.asyncio
async def test_metrics_endpoint():
    from httpx import ASGITransport, AsyncClient

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        r = await client.get("/metrics")
    assert r.status_code in (200, 503)
    text = r.text
    assert "blop_runs_total" in text or "prometheus_client not installed" in text
