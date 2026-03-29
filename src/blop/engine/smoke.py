"""Deterministic bounded smoke-preflight probes for release replay."""

from __future__ import annotations

import asyncio
from urllib.parse import urljoin

from playwright.async_api import async_playwright

from blop.engine.dom_utils import extract_interactive_nodes_flat
from blop.schemas import SmokeFinding, SmokeSummary

_AUTH_REDIRECT_MARKERS = ("/login", "/signin", "/sign-in", "/auth", "oauth")


def _distinct_probe_urls(app_url: str, flows: list) -> list[tuple[str, object | None]]:
    seen: set[str] = set()
    probes: list[tuple[str, object | None]] = []
    for url, flow in [(app_url, None), *[(getattr(flow, "entry_url", None) or app_url, flow) for flow in flows[:3]]]:
        resolved = urljoin(app_url, url) if url and url.startswith("/") else (url or app_url)
        if not resolved or resolved in seen:
            continue
        seen.add(resolved)
        probes.append((resolved, flow))
    return probes


async def _probe_url(
    *,
    app_url: str,
    url: str,
    profile_name: str | None,
    flow=None,
    timeout_ms: int = 12000,
) -> dict:
    from blop.engine.auth import resolve_storage_state_for_profile

    storage_state = await resolve_storage_state_for_profile(profile_name, allow_auto_env=False)
    console_errors: list[str] = []
    server_errors: list[str] = []
    final_url = url
    interactive_nodes: list[dict] = []
    findings: list[SmokeFinding] = []

    pw = await async_playwright().start()
    browser = await pw.chromium.launch(headless=True)
    context = await browser.new_context(storage_state=storage_state) if storage_state else await browser.new_context()
    page = await context.new_page()
    page.on("console", lambda msg: console_errors.append(msg.text) if msg.type == "error" else None)
    page.on("response", lambda resp: server_errors.append(f"{resp.status} {resp.url}") if resp.status >= 500 else None)

    try:
        await page.goto(url, wait_until="domcontentloaded", timeout=timeout_ms)
        await page.wait_for_timeout(1000)
        final_url = page.url or url
        try:
            snapshot = await page.accessibility.snapshot(interesting_only=True)
        except Exception:
            snapshot = None
        if snapshot:
            interactive_nodes = extract_interactive_nodes_flat(snapshot, max_nodes=20)

        lowered = (final_url or "").lower()
        if any(marker in lowered for marker in _AUTH_REDIRECT_MARKERS):
            findings.append(
                SmokeFinding(
                    kind="auth_redirect",
                    severity="high",
                    message=f"Probe landed on auth redirect: {final_url}",
                    url=final_url,
                    flow_id=getattr(flow, "flow_id", None),
                    flow_name=getattr(flow, "flow_name", None),
                )
            )
        for error in console_errors[:3]:
            findings.append(
                SmokeFinding(
                    kind="console_error",
                    severity="medium",
                    message=error[:200],
                    url=final_url,
                    flow_id=getattr(flow, "flow_id", None),
                    flow_name=getattr(flow, "flow_name", None),
                )
            )
        for error in server_errors[:3]:
            findings.append(
                SmokeFinding(
                    kind="server_error",
                    severity="high",
                    message=error[:200],
                    url=final_url,
                    flow_id=getattr(flow, "flow_id", None),
                    flow_name=getattr(flow, "flow_name", None),
                )
            )
        if not interactive_nodes:
            findings.append(
                SmokeFinding(
                    kind="sparse_interactives",
                    severity="low",
                    message="Smoke probe found no interactive accessibility nodes.",
                    url=final_url,
                    flow_id=getattr(flow, "flow_id", None),
                    flow_name=getattr(flow, "flow_name", None),
                )
            )
    except Exception as exc:
        findings.append(
            SmokeFinding(
                kind="navigation_error",
                severity="high",
                message=str(exc),
                url=url,
                flow_id=getattr(flow, "flow_id", None),
                flow_name=getattr(flow, "flow_name", None),
            )
        )
    finally:
        await context.close()
        await browser.close()
        await pw.stop()

    return {
        "url": url,
        "final_url": final_url,
        "interactive_count": len(interactive_nodes),
        "findings": findings,
    }


async def run_smoke_preflight(
    *,
    app_url: str,
    flows: list,
    profile_name: str | None = None,
    concurrency: int = 3,
) -> SmokeSummary:
    """Run a bounded, advisory-only smoke preflight against the root and top flow entries."""
    probes = _distinct_probe_urls(app_url, flows)
    semaphore = asyncio.Semaphore(max(1, min(concurrency, 3)))

    async def _run_one(url: str, flow):
        async with semaphore:
            return await _probe_url(app_url=app_url, url=url, profile_name=profile_name, flow=flow)

    results = await asyncio.gather(*(_run_one(url, flow) for url, flow in probes))
    findings: list[SmokeFinding] = []
    findings_by_kind: dict[str, int] = {}
    for result in results:
        for finding in result["findings"]:
            findings.append(finding)
            findings_by_kind[finding.kind] = findings_by_kind.get(finding.kind, 0) + 1

    status = "clean"
    if findings:
        status = "advisory_findings"
        if any(finding.kind == "navigation_error" for finding in findings):
            status = "probe_error"

    return SmokeSummary(
        status=status,
        probe_count=len(probes),
        findings=findings,
        findings_by_kind=findings_by_kind,
        probed_urls=[str(result["url"]) for result in results],
    )
