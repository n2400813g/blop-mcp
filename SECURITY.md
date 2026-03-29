# Security policy

## Supported versions

Security fixes are applied to the **latest minor release** on the default branch (`main`). Older tags may not receive backports unless explicitly announced in release notes.

## Reporting a vulnerability

Please **do not** open a public GitHub issue for security reports.

1. Prefer [GitHub private vulnerability reporting](https://docs.github.com/en/code-security/security-advisories/guidance-on-reporting-and-writing-information-about-vulnerabilities/privately-reporting-a-security-vulnerability) when the repository has it enabled, **or**
2. Email maintainers privately if a security contact is published on the GitHub org or repository profile.

Include:

- A short description and impact assessment
- Steps to reproduce (proof-of-concept if possible)
- Affected version or commit range if known

We aim to acknowledge reports within **5 business days** and coordinate disclosure after a fix is available.

## Scope (local MCP runtime)

This project runs primarily as a **local process** (stdio MCP) with optional **local** HTTP sidecars. Threat considerations include:

- **Secrets:** LLM API keys, auth `storage_state` files, and environment-based credentials must be protected like any CI secret.
- **SSRF / URL safety:** Production configs should keep URL allowlists restrictive (`BLOP_ALLOW_INTERNAL_URLS=false`, validated `app_url`).
- **Artifact data:** Screenshots, traces, and logs may contain **PII or session data** from target applications; treat `.blop/` and `runs/` as sensitive (see [`docs/DATA_HANDLING.md`](docs/DATA_HANDLING.md)).

## Dependency updates

Automated dependency update PRs are enabled via Dependabot. Critical security patches may be merged outside the weekly cadence.
