import httpx
import pytest
import respx

from blop.sync.client import SyncClient
from blop.sync.models import SyncRunPayload

BASE = "https://app.blop.ai"
TOKEN = "blop_sk_test123"


@pytest.fixture
def client():
    return SyncClient(base_url=BASE, api_token=TOKEN)


@respx.mock
@pytest.mark.asyncio
async def test_push_run_success(client):
    """push_run posts to /api/v1/sync/runs and returns cloud test_run_id on success."""
    route = respx.post(f"{BASE}/api/v1/sync/runs").mock(
        return_value=httpx.Response(201, json={"status": "completed", "test_run_id": "run-1", "run_cases_stored": 0})
    )
    payload = SyncRunPayload(
        blop_mcp_run_id="mcp-run-1",
        project_id="proj-1",
        url="https://example.com",
        runtime_contract_version="2026-03-29",
        run_cases=[],
    )
    result = await client.push_run(payload)
    assert result == "run-1"
    assert route.called


@respx.mock
@pytest.mark.asyncio
async def test_push_run_never_raises_on_network_error(client):
    """push_run is fire-and-forget: network failures must not raise."""
    respx.post(f"{BASE}/api/v1/sync/runs").mock(side_effect=httpx.ConnectError("down"))
    payload = SyncRunPayload(
        blop_mcp_run_id="mcp-run-2",
        project_id="proj-1",
        url="https://example.com",
        runtime_contract_version="2026-03-29",
        run_cases=[],
    )
    result = await client.push_run(payload)  # must not raise
    assert result is None


@respx.mock
@pytest.mark.asyncio
async def test_probe_connection_validates_token(client):
    """probe_connection returns True when API responds 200."""
    respx.get(f"{BASE}/api/v1/sync/connection").mock(
        return_value=httpx.Response(200, json={"status": "ok", "workspace_id": "ws-1"})
    )
    ok = await client.probe_connection()
    assert ok is True


@respx.mock
@pytest.mark.asyncio
async def test_push_artifacts_uploads_artifact_list(client):
    """push_artifacts POSTs batch payload to .../artifacts/batch."""
    run_id = "cloud-run-id-abc"
    route = respx.post(f"{BASE}/api/v1/sync/runs/{run_id}/artifacts/batch").mock(
        return_value=httpx.Response(
            201,
            json={
                "stored": 2,
                "results": [
                    {"artifact_id": "a1", "test_run_id": run_id, "artifact_key": "screenshot_step_001.png"},
                    {"artifact_id": "a2", "test_run_id": run_id, "artifact_key": "console.log"},
                ],
            },
        )
    )
    artifacts = [
        {"artifact_key": "screenshot_step_001.png", "kind": "screenshot", "storage_url": "https://example.com/a.png"},
        {"artifact_key": "console.log", "kind": "console_log", "storage_url": "https://example.com/c.log"},
    ]
    result = await client.push_artifacts(cloud_run_id=run_id, artifacts=artifacts)
    assert result is True
    assert route.call_count == 1


@respx.mock
@pytest.mark.asyncio
async def test_push_artifacts_never_raises(client):
    """push_artifacts is fire-and-forget; network errors must not raise."""
    respx.post(f"{BASE}/api/v1/sync/runs/bad-id/artifacts/batch").mock(side_effect=httpx.TimeoutException("timeout"))
    result = await client.push_artifacts(
        cloud_run_id="bad-id",
        artifacts=[{"artifact_key": "x.png", "storage_url": "https://example.com/x.png"}],
    )
    assert result is False
