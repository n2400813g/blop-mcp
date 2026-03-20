"""evaluate_web_task — one-shot browser agent evaluation with rich evidence capture."""
from __future__ import annotations

import asyncio
import json
import os
import re
import time
import uuid
from datetime import datetime, timezone
from typing import Optional

from blop.config import validate_app_url
from blop.engine import auth as auth_engine
from blop.engine.flow_builder import (
    AgentStepInfo,
    build_recorded_flow,
    build_steps_from_agent_actions,
)
from blop.storage import sqlite, files as file_store


async def evaluate_web_task(
    app_url: str,
    task: str,
    profile_name: Optional[str] = None,
    headless: bool = False,
    max_steps: int = 25,
    capture: Optional[list[str]] = None,
    format: str = "markdown",
    save_as_recorded_flow: bool = False,
    flow_name: Optional[str] = None,
) -> dict:
    """Run a browser agent for a natural-language task and return a rich evaluation report.

    Unlike record_test_flow, this does not require a flow_name or goal upfront and
    returns a complete report in a single call (no polling).
    """
    url_err = validate_app_url(app_url)
    if url_err:
        return {"error": url_err}
    if not task or not task.strip():
        return {"error": "task is required"}
    if format not in {"markdown", "text", "json"}:
        return {"error": f"Invalid format '{format}'. Must be one of: markdown, text, json"}

    capture_flags = set(capture or ["screenshots", "console", "network", "trace"])
    valid_flags = {"screenshots", "console", "network", "trace"}
    capture_flags = capture_flags & valid_flags
    if not capture_flags:
        capture_flags = {"screenshots", "console", "network"}

    storage_state: Optional[str] = None
    if profile_name:
        profile = await sqlite.get_auth_profile(profile_name)
        if profile is None:
            return {
                "error": (
                    f"Auth profile '{profile_name}' was not found. "
                    "Provide a valid profile_name or omit it to run without auth."
                )
            }
        try:
            storage_state = await auth_engine.resolve_storage_state(profile)
        except Exception as exc:
            return {
                "error": (
                    f"Auth profile '{profile_name}' could not be resolved: {exc}. "
                    "Refresh credentials or run capture_auth_session."
                )
            }
    if storage_state is None:
        storage_state = await auth_engine.auto_storage_state_from_env()

    run_id = uuid.uuid4().hex
    file_store.ensure_run_dirs(run_id)

    report = await _run_evaluation(
        app_url=app_url,
        task=task,
        run_id=run_id,
        storage_state=storage_state,
        headless=headless,
        max_steps=max_steps,
        capture_flags=capture_flags,
    )

    # Persist the run and artifacts
    await sqlite.create_run(
        run_id=run_id,
        app_url=app_url,
        profile_name=profile_name,
        flow_ids=[],
        headless=headless,
        artifacts_dir=file_store.artifacts_dir(run_id),
        run_mode="evaluate",
    )

    completed_at = datetime.now(timezone.utc).isoformat()
    status = "completed" if report.get("pass_fail") != "error" else "failed"
    await sqlite.update_run_status(run_id, status)

    # Persist network log if captured
    network_log_path = report.get("_network_log_path")
    if network_log_path:
        await sqlite.save_artifact(run_id, None, "network_log", network_log_path)

    for screenshot in report.get("evidence", {}).get("screenshots", []):
        await sqlite.save_artifact(run_id, None, "screenshot", screenshot)

    trace_path = report.get("evidence", {}).get("trace_path")
    if trace_path:
        await sqlite.save_artifact(run_id, None, "trace", trace_path)

    console_log_path = report.get("_console_log_path")
    if console_log_path:
        await sqlite.save_artifact(run_id, None, "console_log", console_log_path)

    # Optionally promote to a recorded flow
    recorded_flow_id = None
    if save_as_recorded_flow:
        recorded_flow_id = await _promote_to_recorded_flow(
            app_url=app_url,
            task=task,
            run_id=run_id,
            agent_steps=report.get("agent_steps", []),
            flow_name=flow_name,
        )

    # Clean up internal keys before returning
    report.pop("_network_log_path", None)
    report.pop("_console_log_path", None)

    report["run_id"] = run_id
    if recorded_flow_id:
        report["recorded_flow_id"] = recorded_flow_id

    # Compute simplified go/no-go recommendation
    pf = report.get("pass_fail", "error")
    evidence = report.get("evidence", {})
    has_console_errors = bool(evidence.get("console_errors"))
    has_network_failures = bool(evidence.get("network_failures"))
    raw = report.get("raw_result", "").lower()
    is_hard_failure = any(kw in raw for kw in ("error", "not found", "failed", "could not"))
    if pf == "pass" and not has_console_errors and not has_network_failures:
        rec_decision = "SHIP"
        rec_rationale = "Task completed successfully with no console or network errors."
    elif pf == "fail" and is_hard_failure:
        rec_decision = "BLOCK"
        rec_rationale = "Task failed with explicit error or missing resource. Investigate before shipping."
    else:
        rec_decision = "INVESTIGATE"
        rec_rationale = "Task encountered issues or errors. Review evidence before shipping."
    report["release_recommendation"] = {
        "decision": rec_decision,
        "confidence": "medium",
        "rationale": rec_rationale,
    }

    if format == "markdown":
        report["formatted_report"] = _format_markdown(report, task, app_url)
    elif format == "text":
        report["formatted_report"] = _format_text(report, task, app_url)
    elif format == "json":
        report["formatted_report"] = json.dumps(report, indent=2)

    return report


async def _run_evaluation(
    app_url: str,
    task: str,
    run_id: str,
    storage_state: Optional[str],
    headless: bool,
    max_steps: int,
    capture_flags: set[str],
) -> dict:
    """Core evaluation loop: launch browser, run agent, collect evidence."""
    from browser_use import Agent, BrowserSession
    from blop.engine.browser import make_browser_profile
    from blop.engine.llm_factory import make_agent_llm
    from blop.engine.recording import SPA_AGENT_RULES

    llm = make_agent_llm()
    browser_profile = make_browser_profile(headless=headless, storage_state=storage_state)
    browser_session = BrowserSession(browser_profile=browser_profile)

    agent_steps: list[AgentStepInfo] = []
    console_logs: list[dict] = []
    console_errors: list[str] = []
    network_requests: list[dict] = []
    network_failures: list[dict] = []
    screenshots: list[str] = []
    trace_path_result: Optional[str] = None
    raw_result = ""
    pass_fail = "error"
    start_time = time.time()

    try:
        from blop.engine.auth_prompt import append_runtime_auth_guidance
        _agent_task = append_runtime_auth_guidance(f"Navigate to {app_url} then: {task}")
        agent = Agent(
            task=_agent_task,
            llm=llm,
            browser_session=browser_session,
            use_vision=True,
            use_judge=False,
            extend_system_message=(
                "You are a QA evaluator. Execute the task thoroughly and report "
                "what you observe. Note any UX issues, errors, or unexpected behavior. "
                + SPA_AGENT_RULES
            ),
        )

        # Set up evidence listeners on the page once context is available
        _listeners_attached = False

        async def _attach_listeners():
            nonlocal _listeners_attached
            if _listeners_attached:
                return
            try:
                ctx = getattr(browser_session, "context", None)
                if ctx and ctx.pages:
                    page = ctx.pages[0]
                    if "console" in capture_flags:
                        page.on("console", lambda msg: _on_console(msg, console_logs, console_errors))
                    if "network" in capture_flags:
                        page.on("response", lambda resp: _on_response(resp, network_requests, network_failures))
                        page.on("requestfailed", lambda req: _on_request_failed(req, network_failures))
                    _listeners_attached = True
            except Exception:
                pass

        # Screenshot polling + listener attachment
        step_idx = [0]

        _MAX_SCREENSHOTS = int(os.getenv("BLOP_MAX_SCREENSHOTS", "50"))

        async def _poll_screenshots():
            while True:
                try:
                    await asyncio.sleep(3)
                    await _attach_listeners()
                    if "screenshots" in capture_flags:
                        if step_idx[0] >= _MAX_SCREENSHOTS:
                            break
                        ctx = getattr(browser_session, "context", None)
                        if ctx and ctx.pages:
                            shot_path = file_store.screenshot_path(run_id, "eval", step_idx[0])
                            await ctx.pages[0].screenshot(path=shot_path)
                            screenshots.append(shot_path)
                            step_idx[0] += 1
                except asyncio.CancelledError:
                    break
                except Exception:
                    pass

        screenshot_task: asyncio.Task | None = None

        # Start tracing if requested
        tracing_started = False
        if "trace" in capture_flags:
            try:
                ctx = getattr(browser_session, "context", None)
                if ctx:
                    await ctx.tracing.start(screenshots=True, snapshots=True, sources=False)
                    tracing_started = True
            except Exception:
                pass

        screenshot_task = asyncio.create_task(_poll_screenshots())

        try:
            history = await agent.run(max_steps=max_steps)

            # Final screenshot
            try:
                ctx = getattr(browser_session, "context", None)
                if ctx and ctx.pages:
                    final_path = file_store.screenshot_path(run_id, "eval", 999)
                    await ctx.pages[0].screenshot(path=final_path)
                    screenshots.append(final_path)
            except Exception:
                pass

            # Extract agent actions
            if hasattr(history, "model_actions"):
                for i, action in enumerate(history.model_actions()):
                    step_desc = _summarize_action(action, i)
                    if step_desc:
                        agent_steps.append(step_desc)

            # Determine pass/fail from agent output
            raw_result = ""
            done_success = True
            if hasattr(history, "model_actions"):
                try:
                    for action in reversed(history.model_actions()):
                        done_val = action.get("done") if isinstance(action, dict) else None
                        if done_val is not None:
                            if isinstance(done_val, dict):
                                raw_result = str(done_val.get("text") or done_val)
                                done_success = bool(done_val.get("success", True))
                            else:
                                raw_result = str(done_val)
                            break
                except Exception:
                    pass

            if not raw_result:
                raw_result = str(history.final_result()) if hasattr(history, "final_result") else str(history)

            pass_fail = "pass" if done_success else "fail"

            # Check for hard browser failures
            try:
                ctx = getattr(browser_session, "context", None)
                if ctx and ctx.pages:
                    page = ctx.pages[0]
                    title_text = (await page.title() or "").lower()
                    try:
                        h1_text = (await page.inner_text("h1") or "").lower()
                    except Exception:
                        h1_text = ""

                    status_code_re = re.compile(r"\b(?:404|500)\b")
                    error_phrase_re = re.compile(r"\b(?:page not found|internal server error)\b")
                    if (
                        status_code_re.search(title_text)
                        or status_code_re.search(h1_text)
                        or error_phrase_re.search(title_text)
                        or error_phrase_re.search(h1_text)
                    ):
                        pass_fail = "fail"
            except Exception:
                pass

        finally:
            if screenshot_task:
                screenshot_task.cancel()
                try:
                    await screenshot_task
                except asyncio.CancelledError:
                    pass

            # Stop tracing
            if tracing_started:
                try:
                    ctx = getattr(browser_session, "context", None)
                    if ctx:
                        tp = file_store.trace_path(run_id, "eval")
                        await ctx.tracing.stop(path=tp)
                        trace_path_result = tp
                except Exception:
                    pass

    except Exception as e:
        raw_result = str(e)
        pass_fail = "error"
    finally:
        try:
            await browser_session.aclose()
        except Exception:
            pass

    elapsed_secs = round(time.time() - start_time, 1)

    # Generate summary via LLM if we have results
    summary = _generate_summary_from_result(raw_result, task, pass_fail)

    # Persist console log
    console_log_path = None
    if console_errors or console_logs:
        console_log_path = file_store.console_log_path(run_id, "eval")
        with open(console_log_path, "w") as f:
            for entry in console_logs:
                f.write(f"[{entry.get('type', 'log')}] {entry.get('text', '')}\n")

    # Persist network log
    network_log_path = None
    if network_requests or network_failures:
        net_dir = file_store._runs_dir() / "network" / run_id
        net_dir.mkdir(parents=True, exist_ok=True)
        network_log_path = str(net_dir / "requests.jsonl")
        with open(network_log_path, "w") as f:
            for req in network_requests:
                f.write(json.dumps(req) + "\n")

    return {
        "summary": summary,
        "agent_steps": agent_steps,
        "evidence": {
            "console_errors": console_errors[:30],
            "console_log_count": len(console_logs),
            "network_failures": network_failures[:20],
            "network_request_count": len(network_requests),
            "screenshots": screenshots,
            "trace_path": trace_path_result,
        },
        "pass_fail": pass_fail,
        "raw_result": raw_result[:2000],
        "elapsed_secs": elapsed_secs,
        "_network_log_path": network_log_path,
        "_console_log_path": console_log_path,
    }


def _on_console(msg, console_logs: list, console_errors: list) -> None:
    entry = {"type": msg.type, "text": msg.text, "ts": time.time()}
    console_logs.append(entry)
    if msg.type == "error":
        console_errors.append(msg.text)


def _on_response(resp, network_requests: list, network_failures: list) -> None:
    entry = {
        "method": resp.request.method,
        "url": resp.url,
        "status": resp.status,
        "ts": time.time(),
    }
    # Filter out noisy static asset requests
    url_lower = resp.url.lower()
    skip_extensions = (".png", ".jpg", ".jpeg", ".gif", ".svg", ".ico", ".woff", ".woff2", ".ttf", ".css")
    if not any(url_lower.endswith(ext) for ext in skip_extensions):
        network_requests.append(entry)
    if resp.status >= 400:
        network_failures.append(entry)


def _on_request_failed(req, network_failures: list) -> None:
    network_failures.append({
        "method": req.method,
        "url": req.url,
        "status": 0,
        "failure": req.failure or "unknown",
        "ts": time.time(),
    })


def _summarize_action(action, index: int) -> Optional[AgentStepInfo]:
    """Convert a browser-use model action dict into a compact step summary."""
    if not isinstance(action, dict):
        return None
    action_name = next(
        (k for k, v in action.items() if k != "interacted_element" and v is not None),
        None,
    )
    if not action_name:
        return None
    params = action.get(action_name) or {}
    desc = ""
    if action_name == "go_to_url" or action_name == "navigate":
        url = params.get("url", "") if isinstance(params, dict) else str(params)
        desc = f"Navigate -> {url}"
    elif action_name == "click_element":
        idx = params.get("index", "?") if isinstance(params, dict) else "?"
        desc = f"Click element (index {idx})"
    elif action_name == "input_text":
        text = params.get("text", "") if isinstance(params, dict) else str(params)
        desc = f"Type \"{text[:50]}\""
    elif action_name == "done":
        text = params.get("text", "") if isinstance(params, dict) else str(params)
        desc = f"Done: {str(text)[:100]}"
    else:
        desc = f"{action_name}: {str(params)[:80]}"

    return {"step": index + 1, "action": action_name, "description": desc}


def _generate_summary_from_result(raw_result: str, task: str, pass_fail: str) -> list[str]:
    """Generate 1-3 summary bullet points from the agent result."""
    bullets = []
    if pass_fail == "pass":
        bullets.append(f"Task completed successfully: {task[:100]}")
    elif pass_fail == "fail":
        bullets.append(f"Task encountered issues: {task[:100]}")
    else:
        bullets.append(f"Task errored: {task[:100]}")

    if raw_result:
        snippet = raw_result[:200].strip()
        if snippet:
            bullets.append(f"Agent conclusion: {snippet}")

    return bullets


async def _promote_to_recorded_flow(
    app_url: str,
    task: str,
    run_id: str,
    agent_steps: list[AgentStepInfo],
    flow_name: Optional[str] = None,
) -> Optional[str]:
    """Convert evaluation agent steps into a RecordedFlow and persist it."""
    if not agent_steps:
        return None

    name = flow_name or f"eval_{run_id[:8]}"
    steps = build_steps_from_agent_actions(
        app_url=app_url,
        final_assertion=task,
        agent_steps=agent_steps,
        map_action=_map_eval_action,
    )
    flow = build_recorded_flow(
        flow_name=name,
        app_url=app_url,
        goal=task,
        steps=steps,
        assertions_json=[task],
        entry_url=app_url,
    )
    await sqlite.save_flow(flow)
    return flow.flow_id


def _map_eval_action(action_name: str) -> Optional[str]:
    """Map browser-use action names to FlowStep action types."""
    mapping = {
        "click_element": "click",
        "input_text": "fill",
        "go_to_url": "navigate",
        "navigate": "navigate",
        "select_dropdown_option": "select",
    }
    if action_name in ("done", "extract_page_content", "screenshot"):
        return None
    return mapping.get(action_name, "click")


def _format_markdown(report: dict, task: str, app_url: str) -> str:
    """Format the evaluation report as a pasteable markdown block."""
    lines = []
    lines.append(f"## Web Evaluation Report for {app_url}")
    lines.append(f"**Task:** {task}")
    lines.append("")

    pf = report.get("pass_fail", "unknown")
    lines.append(f"**Result:** {pf.upper()} ({report.get('elapsed_secs', '?')}s)")
    lines.append("")

    rec = report.get("release_recommendation", {})
    if rec:
        decision = rec.get("decision", "INVESTIGATE")
        confidence = rec.get("confidence", "medium")
        rationale = rec.get("rationale", "")
        decision_icon = {"SHIP": "✅", "INVESTIGATE": "⚠️", "BLOCK": "🚫"}.get(decision, "❓")
        lines.append(f"### Release Recommendation: {decision_icon} {decision} (confidence: {confidence})")
        if rationale:
            lines.append(f"> {rationale}")
        lines.append("")

    summary = report.get("summary", [])
    if summary:
        lines.append("### Summary")
        for bullet in summary:
            lines.append(f"- {bullet}")
        lines.append("")

    steps = report.get("agent_steps", [])
    if steps:
        lines.append("### Agent Steps")
        for s in steps[:30]:
            lines.append(f"  {s['step']}. {s['description']}")
        lines.append("")

    evidence = report.get("evidence", {})

    console_errors = evidence.get("console_errors", [])
    if console_errors:
        lines.append(f"### Console Errors ({len(console_errors)})")
        for i, err in enumerate(console_errors[:10], 1):
            lines.append(f"  {i}. {err[:200]}")
        lines.append("")

    net_failures = evidence.get("network_failures", [])
    if net_failures:
        lines.append(f"### Network Failures ({len(net_failures)})")
        for i, nf in enumerate(net_failures[:10], 1):
            lines.append(f"  {i}. {nf.get('method', '?')} {nf.get('url', '?')} -> {nf.get('status', '?')}")
        lines.append("")

    lines.append(f"**Network requests captured:** {evidence.get('network_request_count', 0)}")
    lines.append(f"**Console logs captured:** {evidence.get('console_log_count', 0)}")
    lines.append(f"**Screenshots:** {len(evidence.get('screenshots', []))}")

    if evidence.get("trace_path"):
        lines.append(f"**Trace:** {evidence['trace_path']}")

    lines.append(f"\n**Run ID:** `{report.get('run_id', '?')}`")

    return "\n".join(lines)


def _format_text(report: dict, task: str, app_url: str) -> str:
    """Format the evaluation report as plain text."""
    lines = []
    lines.append(f"Web Evaluation Report for {app_url}")
    lines.append(f"Task: {task}")
    lines.append(f"Result: {report.get('pass_fail', 'unknown').upper()} ({report.get('elapsed_secs', '?')}s)")
    lines.append("")

    for bullet in report.get("summary", []):
        lines.append(f"- {bullet}")
    lines.append("")

    steps = report.get("agent_steps", [])
    if steps:
        lines.append("Agent Steps:")
        for s in steps[:30]:
            lines.append(f"  {s['step']}. {s['description']}")
        lines.append("")

    evidence = report.get("evidence", {})
    console_errors = evidence.get("console_errors", [])
    if console_errors:
        lines.append(f"Console Errors ({len(console_errors)}):")
        for i, err in enumerate(console_errors[:10], 1):
            lines.append(f"  {i}. {err[:200]}")

    net_failures = evidence.get("network_failures", [])
    if net_failures:
        lines.append(f"Network Failures ({len(net_failures)}):")
        for i, nf in enumerate(net_failures[:10], 1):
            lines.append(f"  {i}. {nf.get('method', '?')} {nf.get('url', '?')} -> {nf.get('status', '?')}")

    lines.append(f"Run ID: {report.get('run_id', '?')}")
    return "\n".join(lines)
