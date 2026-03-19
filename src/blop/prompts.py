"""Centralised prompt templates for all LLM calls.

Prompts can be overridden by placing files in .blop/prompts/ (or BLOP_PROMPTS_DIR).
File names map to variable names: e.g. .blop/prompts/discover.txt overrides DISCOVER_PROMPT.
"""
from __future__ import annotations

import os
from pathlib import Path


def _load_prompt_override(name: str) -> str | None:
    """Load a prompt override from the prompts directory if it exists."""
    from blop.config import BLOP_PROMPTS_DIR

    # Reject path traversal and nested-path names up front.
    if not name or ".." in name or Path(name).name != name:
        return None
    if os.sep in name or (os.altsep and os.altsep in name):
        return None

    prompts_dir = BLOP_PROMPTS_DIR
    if not prompts_dir:
        repo_root = Path(__file__).parent.parent.parent
        prompts_dir = str(repo_root / ".blop" / "prompts")

    prompts_root = Path(prompts_dir).resolve()
    target = (Path(prompts_dir) / f"{name}.txt").resolve()
    try:
        target.relative_to(prompts_root)
    except ValueError:
        return None

    if target.exists():
        return target.read_text(encoding="utf-8").strip()
    return None


def get_prompt(name: str, default: str) -> str:
    """Return the prompt for *name*, checking overrides first."""
    override = _load_prompt_override(name)
    return override if override else default


def list_available_prompts() -> dict[str, str]:
    """Return a map of prompt name -> first 100 chars for resource listing."""
    prompts = {
        "discover": DISCOVER_PROMPT[:100],
        "repair": REPAIR_STEP_PROMPT[:100],
        "remediation": REMEDIATION_PROMPT[:100],
        "next_actions": NEXT_ACTIONS_PROMPT[:100],
    }
    from blop.config import BLOP_PROMPTS_DIR
    prompts_dir = BLOP_PROMPTS_DIR
    if not prompts_dir:
        repo_root = Path(__file__).parent.parent.parent
        prompts_dir = str(repo_root / ".blop" / "prompts")
    d = Path(prompts_dir)
    if d.exists():
        for f in d.glob("*.txt"):
            name = f.stem
            prompts[name] = f.read_text(encoding="utf-8")[:100]
    return prompts

DISCOVER_PROMPT = """You are a senior QA engineer generating browser test flows for a web application.

Application URL: {app_url}

Page inventory (from a depth-2 crawl):
{inventory_text}
{extra_context}

Generate 5-8 meaningful browser test flows. Each flow must test a real user journey, not a generic check.

Rules:
- Use named routes, CTAs, auth links, pricing, contact, onboarding, and integrations as signals
- If auth signals exist (sign in, login, dashboard), include at least one auth flow
- If pricing or contact routes exist, include flows for those
- REJECT generic flows like "page_loads", "nav_links", or "forms_work" unless no richer signal exists
- Each flow must have a concrete, observable outcome

For each flow return:
- flow_name: short snake_case identifier (e.g. "user_login", "checkout_flow")
- goal: one-sentence plain-English user goal
- starting_url: the URL where this flow begins
- preconditions: list of setup requirements (e.g. ["user is logged in"])
- likely_assertions: list of 1-3 specific, verifiable assertions
- severity_if_broken: "blocker" | "high" | "medium" | "low"
- confidence: float 0.0-1.0 representing how confident you are this flow exists
- business_criticality: "revenue" | "activation" | "retention" | "support" | "other"
  - revenue: flows involving checkout, billing, payments, upgrades, subscriptions
  - activation: flows involving signup, onboarding, first-time setup, first value moment
  - retention: flows involving dashboard usage, core product features, settings
  - support: flows involving help, docs, contact
  - other: anything else

Return ONLY a JSON array, no other text:
[{{"flow_name": "...", "goal": "...", "starting_url": "...", "preconditions": [], "likely_assertions": ["..."], "severity_if_broken": "high", "confidence": 0.85, "business_criticality": "revenue"}}]"""


REPAIR_STEP_PROMPT = """You are a browser automation expert repairing a broken test step.

The following test step failed to execute:
- Action: {action}
- Original selector: {selector}
- Target text: {target_text}
- Step description: {description}
- Current URL: {current_url}
{aria_section}
The current page screenshot is also attached.

Provide a repaired action that will accomplish the same goal.

If an ARIA tree is provided above, prefer selecting an element by role+name from it.
Otherwise use the screenshot to find the element.

Return ONLY a JSON object:
{{
  "repaired_locator_type": "css|role|text|label",
  "repaired_selector": "...",
  "repaired_role": "...",
  "repaired_name": "...",
  "repaired_action": "click|fill|navigate",
  "repaired_value": "...",
  "verification_assertion": "..."
}}

If the element is not visible, set repaired_selector to null and repaired_role to null."""


REMEDIATION_PROMPT = """You are a senior QA engineer drafting a bug report and remediation plan for a recurring incident cluster.

Cluster title: {title}
Severity: {severity}
Affected flows: {affected_flows}
Criticality buckets: {criticality_buckets}
Evidence refs: {evidence}
Console errors: {console_errors}
Network errors: {network_errors}

Generate a remediation draft with the following fields:
- issue_body: 2-3 sentence description of what is failing and the likely user impact
- fix_hypotheses: list of exactly 3 concrete fix hypotheses ordered by likelihood
- owner_hint: which team or domain likely owns this (e.g. "Frontend team — checkout UI", "Backend team — payment API")

Return ONLY a JSON object:
{{
  "issue_body": "...",
  "fix_hypotheses": ["Fix 1", "Fix 2", "Fix 3"],
  "owner_hint": "..."
}}"""


NEXT_ACTIONS_PROMPT = """You are a QA engineer explaining a test failure in plain English.

Test flow: {flow_name}
Goal: {goal}
Step that failed: Step {step_index} — {step_description}
Failure mode: {replay_mode}
Assertion failures: {assertion_failures}
Console errors: {console_errors}

Explain in 2-3 sentences:
1. What went wrong
2. Why this matters to the user
3. The most likely fix

Then provide 3 concrete, actionable fix suggestions.

Return ONLY a JSON object:
{{
  "why_failed": "...",
  "next_actions": ["Fix 1", "Fix 2", "Fix 3"]
}}"""
