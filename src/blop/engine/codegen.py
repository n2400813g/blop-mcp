"""Code generation from recordings — export flows as Playwright Python/TS scripts."""

from __future__ import annotations

import asyncio
import re

from blop.schemas import FlowStep, RecordedFlow
from blop.storage.files import codegen_path


def _esc_py(s: str | None) -> str:
    """Escape a string for use inside a Python double-quoted string literal."""
    if not s:
        return ""
    return s.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n").replace("\r", "\\r")


def _esc_ts(s: str | None) -> str:
    """Escape a string for use inside a TypeScript single-quoted string literal."""
    if not s:
        return ""
    return s.replace("\\", "\\\\").replace("'", "\\'").replace("\n", "\\n").replace("\r", "\\r")


def _esc_re(s: str | None) -> str:
    """Escape a string for use inside a regex literal (JS) or re.compile pattern."""
    if not s:
        return ""
    import re

    return re.escape(s)


def _sanitize_identifier(value: str, *, lowercase: bool = True) -> str:
    """Return a safe Python identifier derived from arbitrary text."""
    sanitized = re.sub(r"[^0-9A-Za-z]+", "_", value or "")
    sanitized = re.sub(r"_+", "_", sanitized).strip("_")
    if lowercase:
        sanitized = sanitized.lower()
    if not sanitized:
        sanitized = "flow"
    if sanitized[0].isdigit():
        sanitized = f"_{sanitized}"
    return sanitized


def _locator_expr(step: FlowStep, lang: str = "python") -> str:
    """Generate the best locator expression for a step."""
    if step.testid_selector:
        sel = _esc_py(step.testid_selector) if lang == "python" else _esc_ts(step.testid_selector)
        return f'page.locator("{sel}")' if lang == "python" else f"page.locator('{sel}')"
    if step.aria_role and step.aria_name:
        if lang == "python":
            return f'page.get_by_role("{_esc_py(step.aria_role)}", name="{_esc_py(step.aria_name)}")'
        return f"page.getByRole('{_esc_ts(step.aria_role)}', {{ name: '{_esc_ts(step.aria_name)}' }})"
    if step.label_text and step.action in ("fill", "upload"):
        if lang == "python":
            return f'page.get_by_label("{_esc_py(step.label_text)}")'
        return f"page.getByLabel('{_esc_ts(step.label_text)}')"
    if step.selector:
        if lang == "python":
            return f'page.locator("{_esc_py(step.selector)}")'
        return f"page.locator('{_esc_ts(step.selector)}')"
    if step.target_text:
        if lang == "python":
            return f'page.get_by_text("{_esc_py(step.target_text)}")'
        return f"page.getByText('{_esc_ts(step.target_text)}')"
    if lang == "python":
        return 'page.locator("body")  # TODO: add proper locator'
    return "page.locator('body')  // TODO: add proper locator"


def generate_python(flow: RecordedFlow) -> str:
    """Generate a standalone Playwright Python test from a recorded flow."""
    lines: list[str] = []
    fn_name = f"test_{_sanitize_identifier(flow.flow_name)}"
    lines.append('"""Auto-generated Playwright test from blop recording.')
    lines.append(f"Flow: {flow.flow_name}")
    lines.append(f"Goal: {flow.goal}")
    lines.append(f"App URL: {flow.app_url}")
    lines.append('"""')
    lines.append("import re")
    lines.append("from playwright.sync_api import Playwright, sync_playwright, expect")
    lines.append("")
    lines.append("")
    lines.append(f"def {fn_name}(page):")
    lines.append(f'    """Test: {flow.goal}"""')

    for step in flow.steps:
        loc = _locator_expr(step, "python")
        if step.action == "navigate":
            url = _esc_py(step.value or step.description or flow.app_url)
            lines.append(f'    page.goto("{url}")')
        elif step.action == "click":
            lines.append(f"    {loc}.click()")
        elif step.action == "fill":
            lines.append(f'    {loc}.fill("{_esc_py(step.value)}")')
        elif step.action == "select":
            lines.append(f'    {loc}.select_option("{_esc_py(step.value)}")')
        elif step.action == "upload":
            lines.append(f'    {loc}.set_input_files("{_esc_py(step.value)}")')
        elif step.action == "assert":
            if step.structured_assertion:
                sa = step.structured_assertion
                if sa.assertion_type == "text_present":
                    lines.append(f'    expect(page.get_by_text("{_esc_py(sa.expected)}")).to_be_visible()')
                elif sa.assertion_type == "element_visible":
                    lines.append(f'    expect(page.locator("{_esc_py(sa.target) or "body"}")).to_be_visible()')
                elif sa.assertion_type == "url_contains":
                    lines.append(f'    expect(page).to_have_url(re.compile(r".*{_esc_re(sa.expected)}.*"))')
                elif sa.assertion_type == "page_title":
                    lines.append(f'    expect(page).to_have_title(re.compile(r".*{_esc_re(sa.expected)}.*"))')
                else:
                    lines.append(f"    # TODO: manual assertion — {step.description or step.value}")
            else:
                lines.append(f"    # Assertion: {step.description or step.value}")
        elif step.action == "wait":
            try:
                wait_ms = int(float(step.value or "1") * 1000)
            except ValueError:
                wait_ms = 1000
            lines.append(f"    page.wait_for_timeout({wait_ms})")
        else:
            lines.append(f"    # Unsupported action: {step.action} — {step.description}")

        if step.wait_after_secs > 0 and step.action not in ("wait", "assert"):
            lines.append(f"    page.wait_for_timeout({int(step.wait_after_secs * 1000)})")

    lines.append("")
    lines.append("")
    lines.append('if __name__ == "__main__":')
    lines.append("    with sync_playwright() as p:")
    lines.append("        browser = p.chromium.launch(headless=False)")
    lines.append("        page = browser.new_page()")
    lines.append(f"        {fn_name}(page)")
    lines.append("")
    return "\n".join(lines)


def generate_typescript(flow: RecordedFlow) -> str:
    """Generate a standalone Playwright TypeScript test from a recorded flow."""
    lines: list[str] = []
    fn_name = flow.flow_name.replace("-", "_").replace(" ", "_")
    lines.append("// Auto-generated Playwright test from blop recording")
    lines.append(f"// Flow: {flow.flow_name} | Goal: {flow.goal}")
    lines.append("import { test, expect } from '@playwright/test';")
    lines.append("")
    lines.append(f"test('{_esc_ts(fn_name)} - {_esc_ts(flow.goal)}', async ({{ page }}) => {{")

    for step in flow.steps:
        loc = _locator_expr(step, "typescript")
        if step.action == "navigate":
            url = _esc_ts(step.value or step.description or flow.app_url)
            lines.append(f"  await page.goto('{url}');")
        elif step.action == "click":
            lines.append(f"  await {loc}.click();")
        elif step.action == "fill":
            lines.append(f"  await {loc}.fill('{_esc_ts(step.value)}');")
        elif step.action == "select":
            lines.append(f"  await {loc}.selectOption('{_esc_ts(step.value)}');")
        elif step.action == "upload":
            lines.append(f"  await {loc}.setInputFiles('{_esc_ts(step.value)}');")
        elif step.action == "assert":
            if step.structured_assertion:
                sa = step.structured_assertion
                if sa.assertion_type == "text_present":
                    lines.append(f"  await expect(page.getByText('{_esc_ts(sa.expected)}')).toBeVisible();")
                elif sa.assertion_type == "element_visible":
                    lines.append(f"  await expect(page.locator('{_esc_ts(sa.target) or 'body'}')).toBeVisible();")
                elif sa.assertion_type == "url_contains":
                    lines.append(f"  await expect(page).toHaveURL(/{_esc_re(sa.expected)}/);")
                elif sa.assertion_type == "page_title":
                    lines.append(f"  await expect(page).toHaveTitle(/{_esc_re(sa.expected)}/);")
                else:
                    lines.append(f"  // TODO: manual assertion — {step.description or step.value}")
            else:
                lines.append(f"  // Assertion: {step.description or step.value}")
        elif step.action == "wait":
            try:
                wait_secs = float(step.value) if step.value else 1.0
            except (ValueError, TypeError):
                wait_secs = 1.0
            wait_ms = int(wait_secs * 1000)
            lines.append(f"  await page.waitForTimeout({wait_ms});")
        else:
            lines.append(f"  // Unsupported: {step.action} — {step.description}")

        if step.wait_after_secs > 0 and step.action not in ("wait", "assert"):
            lines.append(f"  await page.waitForTimeout({int(step.wait_after_secs * 1000)});")

    lines.append("});")
    lines.append("")
    return "\n".join(lines)


async def export_flow_as_code(
    flow_id: str,
    language: str = "python",
) -> dict:
    """Export a recorded flow as a standalone Playwright test script."""
    from blop.storage.sqlite import get_flow

    flow = await get_flow(flow_id)
    if not flow:
        return {"error": f"Flow {flow_id} not found"}

    if language == "typescript":
        code = generate_typescript(flow)
        ext = "ts"
    else:
        code = generate_python(flow)
        ext = "py"

    path = codegen_path(flow_id, ext)

    def _write() -> None:
        with open(path, "w") as f:
            f.write(code)

    await asyncio.to_thread(_write)

    return {
        "flow_id": flow_id,
        "flow_name": flow.flow_name,
        "language": language,
        "path": path,
        "step_count": len(flow.steps),
    }
