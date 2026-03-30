"""Fire-and-forget HTTP sync client for pushing blop-mcp run results to hosted blop."""

from __future__ import annotations

import dataclasses
import logging
from pathlib import Path

import httpx

from blop.sync.models import SyncRunPayload

logger = logging.getLogger(__name__)


def _normalize_artifact_upload(item: dict) -> dict[str, str]:
    """Map a flexible artifact dict to hosted ``ArtifactUploadRequest`` fields."""
    path = (item.get("path") or "").strip()
    artifact_key = (item.get("artifact_key") or "").strip()
    if not artifact_key:
        artifact_key = Path(path).name if path else str(item.get("artifact_id") or "artifact")
    artifact_type = str(item.get("artifact_type") or item.get("kind") or "artifact")
    storage_url = (item.get("storage_url") or "").strip()
    if not storage_url:
        if path:
            try:
                storage_url = Path(path).expanduser().resolve().as_uri()
            except OSError:
                storage_url = path
        else:
            storage_url = f"blop-mcp-local://artifact/{artifact_key}"
    return {
        "artifact_type": artifact_type,
        "artifact_key": artifact_key,
        "storage_url": storage_url,
    }


class SyncClient:
    """Posts run results to the hosted blop platform. Never raises — sync is best-effort."""

    def __init__(
        self,
        hosted_url: str | None = None,
        api_token: str | None = None,
        *,
        base_url: str | None = None,
    ) -> None:
        # Accept both hosted_url (legacy) and base_url (new alias)
        self.hosted_url = base_url if base_url is not None else hosted_url
        self.api_token = api_token

    async def probe_connection(self, project_id: str | None = None) -> bool:
        """Validate hosted sync connectivity and token scope.

        Returns True on success, False when sync is not configured or
        when the remote check fails for any reason.
        """
        if not self.hosted_url or not self.api_token:
            return False
        url = f"{self.hosted_url.rstrip('/')}/api/v1/sync/connection"
        params = {"project_id": project_id} if project_id else None
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                resp = await client.get(
                    url,
                    params=params,
                    headers={"Authorization": f"Bearer {self.api_token}"},
                )
                resp.raise_for_status()
                return True
        except Exception as exc:
            logger.warning("blop hosted sync probe failed (non-fatal): %s", exc)
            return False

    async def push_run(self, payload: SyncRunPayload) -> str | None:
        """
        Push a run result to the hosted platform.

        Returns the cloud ``test_run_id`` string on success, or ``None`` if sync is
        not configured or the request fails.
        """
        if not self.hosted_url or not self.api_token:
            return None
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                resp = await client.post(
                    f"{self.hosted_url.rstrip('/')}/api/v1/sync/runs",
                    json=dataclasses.asdict(payload),
                    headers={"Authorization": f"Bearer {self.api_token}"},
                )
                resp.raise_for_status()
                body = resp.json()
                tid = body.get("test_run_id")
                return str(tid) if tid is not None else None
        except Exception as exc:
            logger.warning("blop hosted sync failed (non-fatal): %s", exc)
            return None

    async def push_artifacts(
        self,
        cloud_run_id: str,
        artifacts: list[dict],
    ) -> bool:
        """Upload artifact references via hosted batch endpoint (chunks of 100). Never raises."""
        if not self.hosted_url or not self.api_token:
            return False
        if not artifacts:
            return True
        normalized = [_normalize_artifact_upload(raw) for raw in artifacts]
        batch_url = f"{self.hosted_url.rstrip('/')}/api/v1/sync/runs/{cloud_run_id}/artifacts/batch"
        try:
            async with httpx.AsyncClient(timeout=120) as client:
                chunk_size = 100
                for i in range(0, len(normalized), chunk_size):
                    chunk = normalized[i : i + chunk_size]
                    resp = await client.post(
                        batch_url,
                        json={"artifacts": chunk},
                        headers={"Authorization": f"Bearer {self.api_token}"},
                    )
                    resp.raise_for_status()
            return True
        except Exception as exc:
            logger.warning("blop hosted sync artifacts failed (non-fatal): %s", exc)
            return False
