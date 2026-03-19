"""Security scanning MCP tool wrappers."""
from __future__ import annotations

import ipaddress
import socket
from typing import Optional
from urllib.parse import urlparse


async def security_scan(
    repo_path: str,
    scan_type: str = "semgrep",
    ruleset: str = "p/default",
    severity_filter: Optional[str] = None,
) -> dict:
    """Run a static security scan on a codebase."""
    if scan_type != "semgrep":
        return {"error": f"Unsupported scan type: {scan_type}. Use 'semgrep'."}

    from blop.engine.security import run_semgrep_scan
    return await run_semgrep_scan(
        repo_path=repo_path,
        ruleset=ruleset,
        severity_filter=severity_filter,
    )


async def security_scan_url(
    app_url: str,
    scan_type: str = "headers",
) -> dict:
    """Run a lightweight security scan on a live URL."""
    if scan_type != "headers":
        return {"error": f"Unsupported URL scan type: {scan_type}. Use 'headers'."}

    parsed = urlparse(app_url)
    if parsed.scheme not in {"http", "https"}:
        return {"error": "Invalid URL scheme. Only http and https are allowed."}
    if not parsed.hostname:
        return {"error": "Invalid URL. Hostname is required."}

    try:
        addr_info = socket.getaddrinfo(parsed.hostname, parsed.port or 80)
    except socket.gaierror as e:
        return {"error": f"Could not resolve hostname: {e}"}
    except Exception as e:
        return {"error": f"Failed to validate hostname: {e}"}

    for info in addr_info:
        try:
            ip = ipaddress.ip_address(info[4][0])
        except Exception as e:
            return {"error": f"Failed to parse resolved IP address: {e}"}

        if (
            ip.is_private
            or ip.is_loopback
            or ip.is_link_local
            or ip.is_reserved
            or ip.is_multicast
            or ip.is_unspecified
        ):
            return {"error": f"Refusing to scan non-public address: {ip}"}

    from blop.engine.security import scan_http_headers
    return await scan_http_headers(app_url)
