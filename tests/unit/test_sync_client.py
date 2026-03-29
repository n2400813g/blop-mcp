from unittest.mock import AsyncMock, MagicMock, patch

from blop.sync.client import SyncClient
from blop.sync.models import SyncRunPayload


async def test_sync_noop_when_no_config():
    """No hosted URL or token → returns None without making HTTP request."""
    client = SyncClient(hosted_url=None, api_token=None)
    result = await client.push_run(
        SyncRunPayload(
            blop_mcp_run_id="x",
            project_id="proj-1",
            url="https://example.com",
            run_cases=[],
        )
    )
    assert result is None


async def test_sync_noop_when_no_token():
    """Has URL but no token → still a no-op."""
    client = SyncClient(hosted_url="https://app.blop.dev", api_token=None)
    result = await client.push_run(
        SyncRunPayload(
            blop_mcp_run_id="x",
            project_id="proj-1",
            url="https://example.com",
            run_cases=[],
        )
    )
    assert result is None


async def test_sync_posts_to_endpoint():
    """With both URL and token, POSTs to /api/v1/sync/runs."""
    client = SyncClient(hosted_url="https://app.blop.dev", api_token="blop_sk_test")

    mock_response = MagicMock()
    mock_response.status_code = 201
    mock_response.raise_for_status = MagicMock()
    mock_response.json = MagicMock(
        return_value={"test_run_id": "uuid-123", "status": "completed", "run_cases_stored": 0}
    )

    with patch("httpx.AsyncClient.post", new=AsyncMock(return_value=mock_response)):
        result = await client.push_run(
            SyncRunPayload(
                blop_mcp_run_id="run-abc",
                project_id="proj-1",
                url="https://example.com",
                run_cases=[],
            )
        )

    assert result is not None
    assert result["status"] == "completed"


async def test_sync_does_not_raise_on_http_error():
    """HTTP failure → returns None without raising (fire-and-forget)."""
    client = SyncClient(hosted_url="https://app.blop.dev", api_token="blop_sk_test")

    with patch("httpx.AsyncClient.post", new=AsyncMock(side_effect=Exception("network error"))):
        result = await client.push_run(
            SyncRunPayload(
                blop_mcp_run_id="run-abc",
                project_id="proj-1",
                url="https://example.com",
                run_cases=[],
            )
        )

    assert result is None


async def test_probe_connection_hits_connection_endpoint():
    """Configured client probes the hosted sync connection endpoint."""
    client = SyncClient(hosted_url="https://app.blop.dev", api_token="blop_sk_test")

    mock_response = MagicMock()
    mock_response.raise_for_status = MagicMock()
    mock_response.json = MagicMock(
        return_value={
            "status": "ok",
            "workspace_id": "ws_123",
            "token_scope": "project",
        }
    )

    with patch("httpx.AsyncClient.get", new=AsyncMock(return_value=mock_response)):
        result = await client.probe_connection("proj_123")

    assert result is not None
    assert result["status"] == "ok"


async def test_probe_connection_returns_none_on_failure():
    """Probe failures remain non-fatal for local-first runtime posture."""
    client = SyncClient(hosted_url="https://app.blop.dev", api_token="blop_sk_test")

    with patch("httpx.AsyncClient.get", new=AsyncMock(side_effect=Exception("401"))):
        result = await client.probe_connection("proj_123")

    assert result is None
