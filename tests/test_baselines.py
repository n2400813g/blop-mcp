from __future__ import annotations

import pytest


@pytest.mark.asyncio
async def test_package_authenticated_saas_baseline_builds_strict_step_flows(tmp_db):
    from blop.tools.baselines import package_authenticated_saas_baseline
    from blop.storage import sqlite

    result = await package_authenticated_saas_baseline(
        app_url="https://example.com",
        baseline_name="auth_saas_smoke",
        profile_name="team_login",
        recipes=[
            {
                "recipe_type": "role_click_to_url",
                "flow_name": "blank_project_entry",
                "goal": "Open the blank project path and verify the editor route opens.",
                "business_criticality": "activation",
                "trigger_role": "button",
                "trigger_name": "Create",
                "expected_url_contains": "/editor/",
            },
            {
                "recipe_type": "role_click_to_text",
                "flow_name": "view_plans_modal",
                "goal": "Open pricing and verify the plan chooser is visible.",
                "business_criticality": "revenue",
                "trigger_role": "button",
                "trigger_name": "View Plans",
                "expected_text": "Choose the right plan for you",
            },
        ],
    )

    assert result["status"] == "packaged"
    assert result["flow_count"] == 2
    assert result["recommended_release_check"]["arguments"]["profile_name"] == "team_login"

    flow_one = await sqlite.get_flow(result["release_gate_flow_ids"][0])
    flow_two = await sqlite.get_flow(result["release_gate_flow_ids"][1])

    assert flow_one is not None
    assert flow_two is not None
    assert flow_one.run_mode_override == "strict_steps"
    assert flow_two.run_mode_override == "strict_steps"
    assert flow_one.steps[1].aria_role == "button"
    assert flow_one.steps[-1].structured_assertion.assertion_type == "url_contains"
    assert flow_two.steps[-1].structured_assertion.assertion_type == "text_present"


@pytest.mark.asyncio
async def test_package_authenticated_saas_baseline_supports_curated_selector_recipe(tmp_db):
    from blop.tools.baselines import package_authenticated_saas_baseline
    from blop.storage import sqlite

    result = await package_authenticated_saas_baseline(
        app_url="https://example.com",
        baseline_name="template_baseline",
        recipes=[
            {
                "recipe_type": "selector_then_role_to_url",
                "flow_name": "template_to_editor",
                "goal": "Open the first template and verify editor route opens.",
                "business_criticality": "activation",
                "entry_url": "https://example.com/templates",
                "trigger_selector": ".template-card >> nth=0",
                "follow_up_role": "button",
                "follow_up_name": "Start New Project",
                "expected_url_contains": "/editor/",
            }
        ],
    )

    flow = await sqlite.get_flow(result["release_gate_flow_ids"][0])

    assert flow is not None
    assert flow.steps[1].selector == ".template-card >> nth=0"
    assert flow.steps[3].aria_name == "Start New Project"
    assert flow.steps[-1].structured_assertion.expected == "/editor/"


@pytest.mark.asyncio
async def test_package_authenticated_saas_baseline_supports_text_driven_editor_recipe(tmp_db):
    from blop.tools.baselines import package_authenticated_saas_baseline
    from blop.storage import sqlite

    result = await package_authenticated_saas_baseline(
        app_url="https://example.com",
        baseline_name="editor_panels",
        recipes=[
            {
                "recipe_type": "text_then_text_to_text",
                "flow_name": "recent_project_text_panel",
                "goal": "Open a recent project and verify the Text panel is visible.",
                "business_criticality": "activation",
                "trigger_text": "Untitled Project",
                "follow_up_text": "Text",
                "expected_text": "Add Heading",
            }
        ],
    )

    flow = await sqlite.get_flow(result["release_gate_flow_ids"][0])

    assert flow is not None
    assert flow.steps[1].target_text == "Untitled Project"
    assert flow.steps[3].target_text == "Text"
    assert flow.steps[-1].structured_assertion.expected == "Add Heading"
    assert flow.run_mode_override == "strict_steps"


@pytest.mark.asyncio
async def test_package_authenticated_saas_baseline_supports_text_then_selector_recipe(tmp_db):
    from blop.tools.baselines import package_authenticated_saas_baseline
    from blop.storage import sqlite

    result = await package_authenticated_saas_baseline(
        app_url="https://example.com",
        baseline_name="editor_selector_panels",
        recipes=[
            {
                "recipe_type": "text_then_selector_to_text",
                "flow_name": "recent_project_text_panel_selector",
                "goal": "Open a recent project and verify a panel heading.",
                "business_criticality": "activation",
                "trigger_text": "Untitled Project",
                "follow_up_selector": "div.nav-item:has-text('Text')",
                "expected_text": "Add Heading",
            }
        ],
    )

    flow = await sqlite.get_flow(result["release_gate_flow_ids"][0])

    assert flow is not None
    assert flow.steps[1].target_text == "Untitled Project"
    assert flow.steps[3].selector == "div.nav-item:has-text('Text')"
    assert flow.steps[-1].structured_assertion.expected == "Add Heading"


@pytest.mark.asyncio
async def test_package_authenticated_saas_baseline_validates_recipes(tmp_db):
    from blop.tools.baselines import package_authenticated_saas_baseline

    with pytest.raises(ValueError):
        await package_authenticated_saas_baseline(
            app_url="https://example.com",
            baseline_name="broken",
            recipes=[
                {
                    "recipe_type": "role_click_to_url",
                    "flow_name": "missing_trigger",
                    "goal": "Broken recipe",
                    "expected_url_contains": "/editor/",
                }
            ],
        )
