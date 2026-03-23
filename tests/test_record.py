"""Tests for tools/record.py."""
from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from blop.schemas import FlowStep, RecordedFlow


def _make_steps() -> list[FlowStep]:
    return [
        FlowStep(step_id=0, action="navigate", value="https://example.com"),
        FlowStep(step_id=1, action="click", description="Click submit"),
    ]


@pytest.mark.asyncio
async def test_record_flow_happy_path():
    """Happy path: record_flow returns steps, flow saved, result has status=recorded."""
    from blop.tools.record import record_test_flow

    mock_steps = _make_steps()
    mock_record_flow = AsyncMock(return_value=mock_steps)
    mock_save_flow = AsyncMock()
    mock_get_auth_profile = AsyncMock(return_value=None)
    mock_artifacts_dir = "/tmp/runs/flow_abc123"

    patchers = [
        patch("blop.tools.record.recording.record_flow", mock_record_flow),
        patch("blop.tools.record.sqlite.save_flow", mock_save_flow),
        patch("blop.tools.record.sqlite.get_auth_profile", mock_get_auth_profile),
        patch("blop.tools.record.sqlite.list_flows", new_callable=AsyncMock, return_value=[]),
        patch("blop.tools.record.file_store.artifacts_dir", return_value=mock_artifacts_dir),
        patch("blop.engine.context_graph.detect_app_archetype", return_value="saas_app"),
        patch("blop.engine.context_graph.editor_hints_from_archetype", return_value={}),
        patch(
            "blop.tools.record.auth_engine.auto_storage_state_from_env",
            new=AsyncMock(return_value=None),
        ),
    ]
    for patcher in patchers:
        patcher.start()
    try:
        result = await record_test_flow(
            app_url="https://example.com",
            flow_name="test_flow",
            goal="Complete the form",
        )
    finally:
        for patcher in reversed(patchers):
            patcher.stop()

    assert result["status"] == "recorded"
    assert result["step_count"] == 3
    assert "flow_id" in result
    assert result["flow_name"] == "test_flow"
    assert result["artifacts_dir"] == mock_artifacts_dir
    mock_record_flow.assert_called_once()
    mock_save_flow.assert_called_once()
    saved_flow = mock_save_flow.call_args[0][0]
    assert isinstance(saved_flow, RecordedFlow)
    assert saved_flow.flow_name == "test_flow"
    assert saved_flow.business_criticality == "other"
    assert saved_flow.entry_url == "https://example.com"
    assert saved_flow.assertions_json
    assert all(step.action == "assert" for step in saved_flow.steps[-len(saved_flow.assertions_json):])


@pytest.mark.asyncio
async def test_record_flow_with_business_criticality():
    """business_criticality='revenue' is preserved in the saved flow."""
    from blop.tools.record import record_test_flow

    mock_steps = _make_steps()
    mock_record_flow = AsyncMock(return_value=mock_steps)
    mock_save_flow = AsyncMock()

    with patch("blop.tools.record.recording.record_flow", mock_record_flow):
        with patch("blop.tools.record.sqlite.save_flow", mock_save_flow):
            with patch("blop.tools.record.sqlite.get_auth_profile", AsyncMock(return_value=None)):
                with patch("blop.tools.record.sqlite.list_flows", new_callable=AsyncMock, return_value=[]):
                    with patch("blop.tools.record.file_store.artifacts_dir", return_value="/tmp/runs/f1"):
                        with patch("blop.engine.context_graph.detect_app_archetype", return_value="saas_app"):
                            with patch("blop.engine.context_graph.editor_hints_from_archetype", return_value={}):
                                with patch(
                                    "blop.tools.record.auth_engine.auto_storage_state_from_env",
                                    new=AsyncMock(return_value=None),
                                ):
                                    result = await record_test_flow(
                                        app_url="https://example.com",
                                        flow_name="checkout",
                                        goal="Complete checkout",
                                        business_criticality="revenue",
                                    )

    assert result["status"] == "recorded"
    saved_flow = mock_save_flow.call_args[0][0]
    assert saved_flow.business_criticality == "revenue"


@pytest.mark.asyncio
async def test_record_flow_refreshes_existing_journey_metadata():
    from blop.tools.record import record_test_flow

    mock_steps = _make_steps()
    mock_record_flow = AsyncMock(return_value=mock_steps)
    mock_save_flow = AsyncMock()
    existing = [
        {
            "flow_id": "flow-old",
            "flow_name": "checkout",
            "app_url": "https://example.com",
            "goal": "Complete checkout",
            "created_at": "2026-02-01T10:00:00Z",
        }
    ]

    with patch("blop.tools.record.recording.record_flow", mock_record_flow):
        with patch("blop.tools.record.sqlite.save_flow", mock_save_flow):
            with patch("blop.tools.record.sqlite.get_auth_profile", AsyncMock(return_value=None)):
                with patch("blop.tools.record.sqlite.list_flows", new_callable=AsyncMock, return_value=existing):
                    with patch("blop.tools.record.file_store.artifacts_dir", return_value="/tmp/runs/f1"):
                        with patch("blop.engine.context_graph.detect_app_archetype", return_value="saas_app"):
                            with patch("blop.engine.context_graph.editor_hints_from_archetype", return_value={}):
                                with patch(
                                    "blop.tools.record.auth_engine.auto_storage_state_from_env",
                                    new=AsyncMock(return_value=None),
                                ):
                                    result = await record_test_flow(
                                        app_url="https://example.com",
                                        flow_name="checkout",
                                        goal="Complete checkout",
                                        business_criticality="revenue",
                                    )

    assert result["refresh_summary"]["refresh_detected"] is True
    assert result["refresh_summary"]["previous_flow_id"] == "flow-old"
    assert result["refresh_summary"]["supersedes_previous_recording"] is True
    assert "supersedes flow-old" in result["workflow_hint"]


@pytest.mark.asyncio
async def test_record_flow_invalid_url():
    """Invalid app_url returns error dict with URL validation message."""
    from blop.tools.record import record_test_flow

    result = await record_test_flow(
        app_url="not-a-url",
        flow_name="test_flow",
        goal="Test goal",
    )

    assert "error" in result
    assert "app_url" in result["error"].lower() or "http" in result["error"].lower()


@pytest.mark.asyncio
async def test_record_flow_empty_flow_name():
    """Empty flow_name returns error dict."""
    from blop.tools.record import record_test_flow

    result = await record_test_flow(
        app_url="https://example.com",
        flow_name="",
        goal="Test goal",
    )

    assert result == {"error": "flow_name is required"}


@pytest.mark.asyncio
async def test_record_flow_empty_goal():
    """Empty goal returns error dict."""
    from blop.tools.record import record_test_flow

    result = await record_test_flow(
        app_url="https://example.com",
        flow_name="test_flow",
        goal="",
    )

    assert result == {"error": "goal is required"}


@pytest.mark.asyncio
async def test_record_flow_uses_goal_url_as_entry_url_and_plan_anchor():
    from blop.tools.record import record_test_flow

    mock_steps = [
        FlowStep(step_id=0, action="navigate", value="https://testpages.eviltester.com/"),
        FlowStep(step_id=1, action="click", description="Open text inputs"),
    ]
    mock_save_flow = AsyncMock()

    with patch("blop.tools.record.recording.record_flow", AsyncMock(return_value=mock_steps)):
        with patch("blop.tools.record.sqlite.save_flow", mock_save_flow):
            with patch("blop.tools.record.sqlite.get_auth_profile", AsyncMock(return_value=None)):
                with patch("blop.tools.record.sqlite.list_flows", new_callable=AsyncMock, return_value=[]):
                    with patch("blop.tools.record.file_store.artifacts_dir", return_value="/tmp/runs/f1"):
                        with patch("blop.engine.context_graph.detect_app_archetype", return_value="saas_app"):
                            with patch("blop.engine.context_graph.editor_hints_from_archetype", return_value={}):
                                with patch(
                                    "blop.tools.record.auth_engine.auto_storage_state_from_env",
                                    new=AsyncMock(return_value=None),
                                ):
                                    result = await record_test_flow(
                                        app_url="https://testpages.eviltester.com/",
                                        flow_name="text_inputs",
                                        goal="Navigate to https://testpages.eviltester.com/pages/input-elements/text-inputs/ and verify the text inputs are usable.",
                                        business_criticality="activation",
                                    )

    saved_flow = mock_save_flow.call_args[0][0]
    assert result["status"] == "recorded"
    assert saved_flow.entry_url == "https://testpages.eviltester.com/pages/input-elements/text-inputs/"
    assert saved_flow.intent_contract is not None
    assert saved_flow.intent_contract.target_surface == "public_site"
    assert "https://testpages.eviltester.com/pages/input-elements/text-inputs/" in saved_flow.intent_contract.expected_url_patterns
    assert saved_flow.steps[-1].action == "assert"
    assert any(
        step.structured_assertion and step.structured_assertion.assertion_type == "url_contains"
        for step in saved_flow.steps
        if step.action == "assert"
    )


def test_build_public_page_assertions_prefers_url_and_title_checks_for_unquoted_public_goals():
    from blop.engine.recording import _build_public_page_assertions

    assertions = _build_public_page_assertions(
        goal="Navigate to https://testpages.eviltester.com/ and verify the homepage loads and shows the text Test Pages.",
        current_url="https://testpages.eviltester.com/",
        page_title="Test Pages",
        heading_text="Test Pages",
        page_body_text="Welcome to Test Pages by EvilTester",
    )

    assert assertions
    structured = [item[1] for item in assertions if item[1] is not None]
    kinds = [item.assertion_type for item in structured]
    assert "url_contains" in kinds
    assert "page_title" in kinds
    assert "text_present" not in kinds


def test_build_public_page_assertions_supports_explicit_quoted_text_expectations():
    from blop.engine.recording import _build_public_page_assertions

    assertions = _build_public_page_assertions(
        goal='Navigate to https://testpages.eviltester.com/ and verify the homepage shows the text "Test Pages".',
        current_url="https://testpages.eviltester.com/",
        page_title="Test Pages",
        heading_text="Test Pages",
        page_body_text="Welcome to Test Pages by EvilTester",
    )

    structured = [item[1] for item in assertions if item[1] is not None]
    assert any(item.assertion_type == "text_present" and item.expected == "Test Pages" for item in structured)


def test_build_public_page_assertions_skips_goal_text_when_page_does_not_show_it():
    from blop.engine.recording import _build_public_page_assertions

    assertions = _build_public_page_assertions(
        goal="Navigate to https://testpages.eviltester.com/ and verify the homepage loads and shows the text Test Pages.",
        current_url="https://testpages.eviltester.com/",
        page_title="Home",
        heading_text="Welcome",
        page_body_text="Welcome to the site",
    )

    structured = [item[1] for item in assertions if item[1] is not None]
    assert any(item.assertion_type == "url_contains" for item in structured)
    assert all(not (item.assertion_type == "text_present" and item.expected == "Test Pages") for item in structured)


def test_selector_from_interacted_attrs_prefers_stable_fill_locators():
    from blop.engine.recording import _selector_from_interacted_attrs

    selector = _selector_from_interacted_attrs(
        {
            "id": None,
            "testid": None,
            "placeholder": "Email",
            "name_attr": "email",
            "href": None,
            "input_type": "email",
        },
        "fill",
    )

    assert selector == "[name='email']"


def test_build_public_page_assertions_avoids_inferred_heading_text_when_not_needed():
    from blop.engine.recording import _build_public_page_assertions

    assertions = _build_public_page_assertions(
        goal="Navigate to https://testpages.eviltester.com/ and verify the homepage loads and shows the text Test Pages.",
        current_url="https://testpages.eviltester.com/",
        page_title="TestPages - Web Testing",
        heading_text="",
        page_body_text="",
    )

    structured = [item[1] for item in assertions if item[1] is not None]
    assert any(item.assertion_type == "url_contains" for item in structured)
    assert any(item.assertion_type == "page_title" for item in structured)
    assert all(item.assertion_type != "text_present" for item in structured)


def test_extract_interacted_attrs_from_description_recovers_name_selector():
    from blop.engine.recording import (
        _extract_interacted_attrs_from_description,
        _merge_interacted_hints,
        _selector_from_interacted_attrs,
    )

    description = (
        "{'input': {'index': 3971, 'text': 'Test Text', 'clear': True}, "
        "'interacted_element': DOMInteractedElement(node_id=3915, backend_node_id=3971, "
        "frame_id=None, node_type=<NodeType.ELEMENT_NODE: 1>, node_name='INPUT', "
        "node_value='', parent_backend_node_id=3914, attributes={'type': 'text', 'name': 'textInput'})}"
    )

    attrs = _extract_interacted_attrs_from_description(description)

    assert attrs["name_attr"] == "textInput"
    assert _selector_from_interacted_attrs(attrs, "fill") == "[name='textInput']"
    merged = _merge_interacted_hints({"target_text": "Test Text"}, attrs)
    assert merged["target_text"] == "Test Text"
    assert merged["name_attr"] == "textInput"


def test_extract_interacted_attrs_from_truncated_description_recovers_id_selector():
    from blop.engine.recording import (
        _extract_interacted_attrs_from_description,
        _selector_from_interacted_attrs,
    )

    description = (
        "{'input': {'index': 3963, 'text': 'Test Text', 'clear': True}, "
        "'interacted_element': DOMInteractedElement(node_id=3915, backend_node_id=3963, "
        "frame_id=None, node_type=<NodeType.ELEMENT_NODE: 1>, node_value='', "
        "node_name='INPUT', attributes={'id': 'text-input', 'type': 'text', 'name': 'text'}, "
        "bounds=DOMRect(x=401.0, y=720.9"
    )

    attrs = _extract_interacted_attrs_from_description(description)

    assert attrs["id"] == "text-input"
    assert attrs["name_attr"] == "text"
    assert _selector_from_interacted_attrs(attrs, "fill") == "#text-input"
