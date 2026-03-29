"""Fire-and-forget HTTP sync client for pushing blop-mcp run results to hosted blop."""

import dataclasses
import logging
from typing import Any

import httpx

from blop.sync.models import SyncRunPayload

logger = logging.getLogger(__name__)


class SyncClient:
    """Posts run results to the hosted blop platform. Never raises — sync is best-effort."""

    def __init__(self, hosted_url: str | None, api_token: str | None) -> None:
        self.hosted_url = hosted_url
        self.api_token = api_token

    async def probe_connection(self, project_id: str | None = None) -> dict[str, Any] | None:
        """Validate hosted sync connectivity and token scope.

        Returns the response dict on success, None when sync is not configured or
        when the remote check fails for any reason.
        """
        if not self.hosted_url or not self.api_token:
            return None
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
                return resp.json()
        except Exception as exc:
            logger.warning("blop hosted sync probe failed (non-fatal): %s", exc)
            return None

    async def push_run(self, payload: SyncRunPayload) -> dict[str, Any] | None:
        """
        Push a run result to the hosted platform.
        Returns the response dict on success, None if not configured or on any error.
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
                return resp.json()
        except Exception as exc:
            logger.warning("blop hosted sync failed (non-fatal): %s", exc)
            return None
