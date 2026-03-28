"""Security scanning engine — Semgrep SAST subprocess + HTTP header analysis."""

from __future__ import annotations

import asyncio
import json
import os
import shutil


async def run_semgrep_scan(
    repo_path: str,
    ruleset: str = "p/default",
    severity_filter: str | None = None,
) -> dict:
    """Run Semgrep SAST scan on a directory and return structured findings.

    Semgrep must be installed separately (`pip install semgrep` or `brew install semgrep`).
    """
    if not os.path.isdir(repo_path):
        return {"error": f"Directory not found: {repo_path}"}

    semgrep_bin = shutil.which("semgrep")
    if not semgrep_bin:
        return {
            "error": "Semgrep not found. Install it: pip install semgrep (or brew install semgrep)",
            "install_hint": "pip install semgrep",
        }

    cmd = [semgrep_bin, "--json", "--config", ruleset, repo_path]
    if severity_filter:
        cmd.extend(["--severity", severity_filter.upper()])

    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=300)
        except asyncio.TimeoutError:
            try:
                proc.kill()
            except ProcessLookupError:
                pass
            await proc.wait()
            return {"error": "Semgrep scan timed out after 300 seconds"}

        if proc.returncode not in (0, 1):
            return {
                "error": f"Semgrep exited with code {proc.returncode}",
                "stderr": stderr.decode(errors="replace")[:2000],
            }

        try:
            raw = json.loads(stdout.decode(errors="replace"))
        except json.JSONDecodeError:
            return {"error": "Failed to parse Semgrep JSON output"}

        findings = []
        for result in raw.get("results", []):
            finding = {
                "rule_id": result.get("check_id", ""),
                "severity": result.get("extra", {}).get("severity", "WARNING"),
                "message": result.get("extra", {}).get("message", ""),
                "file": result.get("path", ""),
                "line_start": result.get("start", {}).get("line", 0),
                "line_end": result.get("end", {}).get("line", 0),
                "code_snippet": result.get("extra", {}).get("lines", ""),
                "cwe": result.get("extra", {}).get("metadata", {}).get("cwe", []),
                "fix": result.get("extra", {}).get("fix", ""),
            }
            findings.append(finding)

        return {
            "scan_type": "semgrep",
            "ruleset": ruleset,
            "repo_path": repo_path,
            "finding_count": len(findings),
            "findings": findings,
            "errors": raw.get("errors", [])[:10],
        }

    except Exception as e:
        return {"error": f"Semgrep scan failed: {str(e)}"}


SECURITY_HEADERS = {
    "Strict-Transport-Security": {
        "description": "HTTP Strict Transport Security",
        "recommended": True,
    },
    "Content-Security-Policy": {
        "description": "Content Security Policy",
        "recommended": True,
    },
    "X-Content-Type-Options": {
        "description": "Prevents MIME type sniffing",
        "recommended": True,
        "expected_value": "nosniff",
    },
    "X-Frame-Options": {
        "description": "Clickjacking protection",
        "recommended": True,
    },
    "X-XSS-Protection": {
        "description": "XSS filter (legacy, CSP supersedes)",
        "recommended": False,
    },
    "Referrer-Policy": {
        "description": "Controls referrer information",
        "recommended": True,
    },
    "Permissions-Policy": {
        "description": "Controls browser feature access",
        "recommended": True,
    },
}


async def scan_http_headers(app_url: str) -> dict:
    """Analyze HTTP security headers for a URL.

    Uses Python's built-in urllib to avoid adding a requests dependency.
    """
    import urllib.error
    import urllib.parse
    import urllib.request

    parsed = urllib.parse.urlparse(app_url)
    if parsed.scheme.lower() not in {"http", "https"}:
        return {
            "error": (
                f"Unsupported URL scheme for security header scan: '{parsed.scheme or 'missing'}'. "
                "Only http and https are allowed."
            )
        }

    try:
        req = urllib.request.Request(app_url, method="HEAD")
        req.add_header("User-Agent", "blop-security-scanner/0.3.0")
        with urllib.request.urlopen(req, timeout=15) as resp:
            headers = dict(resp.headers)
    except urllib.error.HTTPError as e:
        headers = dict(e.headers) if e.headers else {}
    except Exception as e:
        return {"error": f"Failed to fetch {app_url}: {str(e)}"}

    header_keys_lower = {k.lower(): v for k, v in headers.items()}
    results = []
    score = 0
    max_score = 0

    for header_name, meta in SECURITY_HEADERS.items():
        if not meta["recommended"]:
            continue
        max_score += 1
        present = header_name.lower() in header_keys_lower
        value = header_keys_lower.get(header_name.lower(), "")
        expected = meta.get("expected_value")

        entry = {
            "header": header_name,
            "description": meta["description"],
            "present": present,
            "value": value if present else None,
        }

        if present:
            score += 1
            if expected and value.lower() != expected.lower():
                entry["warning"] = f"Expected '{expected}', got '{value}'"
        else:
            entry["recommendation"] = f"Add {header_name} header"

        results.append(entry)

    return {
        "scan_type": "headers",
        "app_url": app_url,
        "security_score": round(score / max_score, 2) if max_score > 0 else 0.0,
        "headers_present": score,
        "headers_checked": max_score,
        "results": results,
    }
