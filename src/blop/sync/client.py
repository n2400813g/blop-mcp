"""Fire-and-forget HTTP sync client for pushing blop-mcp run results to hosted blop."""

import dataclasses
import logging

import httpx

from blop.sync.models import SyncRunPayload

logger = logging.getLogger(__name__)


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

    async def push_run(self, payload: SyncRunPayload) -> bool:
        """
        Push a run result to the hosted platform.
        Returns True on success, False if not configured or on any error.
        """
        if not self.hosted_url or not self.api_token:
            return False
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                resp = await client.post(
                    f"{self.hosted_url.rstrip('/')}/api/v1/sync/runs",
                    json=dataclasses.asdict(payload),
                    headers={"Authorization": f"Bearer {self.api_token}"},
                )
                resp.raise_for_status()
                return True
        except Exception as exc:
            logger.warning("blop hosted sync failed (non-fatal): %s", exc)
            return False

    async def push_artifacts(
        self,
        cloud_run_id: str,
        artifacts: list[dict],
    ) -> bool:
        """Upload artifact references for a synced run. Never raises."""
        if not self.hosted_url or not self.api_token:
            return False
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                resp = await client.post(
                    f"{self.hosted_url.rstrip('/')}/api/v1/sync/runs/{cloud_run_id}/artifacts",
                    json={"artifacts": artifacts},
                    headers={"Authorization": f"Bearer {self.api_token}"},
                )
                resp.raise_for_status()
                return True
        except Exception as exc:
            logger.warning("blop hosted sync artifacts failed (non-fatal): %s", exc)
            return False
