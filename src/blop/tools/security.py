"""Security scanning MCP tool wrappers."""

from __future__ import annotations

import ipaddress
import socket
from typing import Optional
from urllib.parse import urlparse

from blop.engine.errors import BLOP_SECURITY_VALIDATION_FAILED, tool_error


async def security_scan(
    repo_path: str,
    scan_type: str = "semgrep",
    ruleset: str = "p/default",
    severity_filter: Optional[str] = None,
) -> dict:
    """Run a static security scan on a codebase."""
    if scan_type != "semgrep":
        return tool_error(
            f"Unsupported scan type: {scan_type}. Use 'semgrep'.",
            BLOP_SECURITY_VALIDATION_FAILED,
            details={"scan_type": scan_type},
        )

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
        return tool_error(
            f"Unsupported URL scan type: {scan_type}. Use 'headers'.",
            BLOP_SECURITY_VALIDATION_FAILED,
            details={"scan_type": scan_type},
        )

    parsed = urlparse(app_url)
    if parsed.scheme not in {"http", "https"}:
        return tool_error(
            "Invalid URL scheme. Only http and https are allowed.",
            BLOP_SECURITY_VALIDATION_FAILED,
        )
    if not parsed.hostname:
        return tool_error("Invalid URL. Hostname is required.", BLOP_SECURITY_VALIDATION_FAILED)

    try:
        addr_info = socket.getaddrinfo(parsed.hostname, parsed.port or 80)
    except socket.gaierror as e:
        return tool_error(f"Could not resolve hostname: {e}", BLOP_SECURITY_VALIDATION_FAILED)
    except Exception as e:
        return tool_error(f"Failed to validate hostname: {e}", BLOP_SECURITY_VALIDATION_FAILED)

    for info in addr_info:
        try:
            ip = ipaddress.ip_address(info[4][0])
        except Exception as e:
            return tool_error(f"Failed to parse resolved IP address: {e}", BLOP_SECURITY_VALIDATION_FAILED)

        if (
            ip.is_private
            or ip.is_loopback
            or ip.is_link_local
            or ip.is_reserved
            or ip.is_multicast
            or ip.is_unspecified
        ):
            return tool_error(f"Refusing to scan non-public address: {ip}", BLOP_SECURITY_VALIDATION_FAILED)

    from blop.engine.security import scan_http_headers

    return await scan_http_headers(app_url)
