"""Tests that codegen properly escapes special characters in generated code."""
from __future__ import annotations

import ast
import pytest

from blop.engine.codegen import _esc_py, _esc_ts, _esc_re, generate_python, generate_typescript
from blop.schemas import FlowStep, RecordedFlow, StructuredAssertion


class TestEscapeHelpers:
    def test_esc_py_double_quotes(self):
        assert _esc_py('test"value') == 'test\\"value'

    def test_esc_py_backslashes(self):
        assert _esc_py("path\\to\\file") == "path\\\\to\\\\file"

    def test_esc_py_newlines(self):
        assert _esc_py("line1\nline2") == "line1\\nline2"

    def test_esc_py_none(self):
        assert _esc_py(None) == ""

    def test_esc_ts_single_quotes(self):
        assert _esc_ts("it's a test") == "it\\'s a test"

    def test_esc_ts_backslashes(self):
        assert _esc_ts("a\\b") == "a\\\\b"

    def test_esc_ts_none(self):
        assert _esc_ts(None) == ""

    def test_esc_re_special_chars(self):
        result = _esc_re("/dashboard?id=1")
        assert "\\?" in result
        assert _esc_re("foo.bar") == "foo\\.bar"

    def test_esc_re_none(self):
        assert _esc_re(None) == ""


class TestGeneratePythonEscaping:
    def _make_flow(self, steps):
        return RecordedFlow(
            flow_name="escape-test",
            app_url="https://example.com",
            goal='Fill a "quoted" value',
            steps=steps,
            created_at="2024-01-01",
        )

    def test_fill_with_double_quotes(self):
        flow = self._make_flow([
            FlowStep(step_id=0, action="fill", selector="#input", value='hello "world"'),
        ])
        code = generate_python(flow)
        assert 'hello \\"world\\"' in code
        # Verify the generated code is syntactically valid Python
        ast.parse(code)

    def test_navigate_with_quotes_in_url(self):
        flow = self._make_flow([
            FlowStep(step_id=0, action="navigate", value='https://example.com/path?q="test"'),
        ])
        code = generate_python(flow)
        assert 'https://example.com/path?q=\\"test\\"' in code
        ast.parse(code)

    def test_selector_with_quotes(self):
        flow = self._make_flow([
            FlowStep(step_id=0, action="click", selector='[data-testid="submit"]', value=None),
        ])
        code = generate_python(flow)
        assert '\\"submit\\"' in code
        ast.parse(code)

    def test_assertion_text_with_quotes(self):
        flow = self._make_flow([
            FlowStep(
                step_id=0,
                action="assert",
                structured_assertion=StructuredAssertion(
                    assertion_type="text_present",
                    expected='Welcome, "Admin"',
                    description="Check welcome message",
                ),
            ),
        ])
        code = generate_python(flow)
        assert 'Welcome, \\"Admin\\"' in code
        ast.parse(code)

    def test_aria_name_with_quotes(self):
        flow = self._make_flow([
            FlowStep(
                step_id=0,
                action="click",
                aria_role="button",
                aria_name='Click "here"',
            ),
        ])
        code = generate_python(flow)
        assert 'Click \\"here\\"' in code
        ast.parse(code)

    def test_label_with_quotes(self):
        flow = self._make_flow([
            FlowStep(step_id=0, action="fill", label_text='Enter "name"', value="test"),
        ])
        code = generate_python(flow)
        assert 'Enter \\"name\\"' in code
        ast.parse(code)


class TestGenerateTypeScriptEscaping:
    def _make_flow(self, steps):
        return RecordedFlow(
            flow_name="ts-escape-test",
            app_url="https://example.com",
            goal="Fill an input with quotes",
            steps=steps,
            created_at="2024-01-01",
        )

    def test_fill_with_single_quotes(self):
        flow = self._make_flow([
            FlowStep(step_id=0, action="fill", selector="#input", value="it's a value"),
        ])
        code = generate_typescript(flow)
        assert "it\\'s a value" in code

    def test_goal_with_single_quotes(self):
        flow = RecordedFlow(
            flow_name="ts-test",
            app_url="https://example.com",
            goal="User's checkout flow",
            steps=[FlowStep(step_id=0, action="navigate", value="https://example.com")],
            created_at="2024-01-01",
        )
        code = generate_typescript(flow)
        assert "User\\'s checkout flow" in code

    def test_selector_with_single_quotes(self):
        flow = self._make_flow([
            FlowStep(step_id=0, action="click", selector="[data-label='submit']", value=None),
        ])
        code = generate_typescript(flow)
        assert "data-label=\\'submit\\'" in code
