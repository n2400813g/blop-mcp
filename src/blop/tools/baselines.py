from __future__ import annotations

from typing import Optional

from blop.config import validate_app_url
from blop.engine.flow_builder import build_recorded_flow
from blop.engine.planner import build_execution_plan, build_intent_contract
from blop.schemas import AuthenticatedBaselineRecipe, FlowStep, StructuredAssertion
from blop.storage import sqlite


def _wait_value(seconds: float) -> str:
    normalized = max(0.1, float(seconds))
    if normalized.is_integer():
        return str(int(normalized))
    return str(normalized)


def _build_steps_for_recipe(app_url: str, recipe: AuthenticatedBaselineRecipe) -> list[FlowStep]:
    entry_url = recipe.entry_url or app_url
    steps: list[FlowStep] = [
        FlowStep(
            step_id=0,
            action="navigate",
            value=entry_url,
            description=f"Navigate to {entry_url}",
            url_after=entry_url,
        )
    ]

    if recipe.recipe_type == "role_click_to_url":
        steps.extend(
            [
                FlowStep(
                    step_id=1,
                    action="click",
                    aria_role=recipe.trigger_role,
                    aria_name=recipe.trigger_name,
                    target_text=recipe.trigger_name,
                    description=f"Click {recipe.trigger_name}",
                ),
                FlowStep(
                    step_id=2,
                    action="wait",
                    value=_wait_value(recipe.wait_before_assert_secs),
                    description="Wait for target route to load",
                ),
                FlowStep(
                    step_id=3,
                    action="assert",
                    value=f"URL contains {recipe.expected_url_contains}",
                    description=f"Verify URL contains {recipe.expected_url_contains}",
                    structured_assertion=StructuredAssertion(
                        assertion_type="url_contains",
                        expected=recipe.expected_url_contains,
                        description=f"Verify URL contains {recipe.expected_url_contains}",
                    ),
                ),
            ]
        )
        return steps

    if recipe.recipe_type == "role_click_to_text":
        steps.extend(
            [
                FlowStep(
                    step_id=1,
                    action="click",
                    aria_role=recipe.trigger_role,
                    aria_name=recipe.trigger_name,
                    target_text=recipe.trigger_name,
                    description=f"Click {recipe.trigger_name}",
                ),
                FlowStep(
                    step_id=2,
                    action="wait",
                    value=_wait_value(recipe.wait_before_assert_secs),
                    description="Wait for modal or gated surface to settle",
                ),
                FlowStep(
                    step_id=3,
                    action="assert",
                    value=recipe.expected_text,
                    description=f"Verify text is visible: {recipe.expected_text}",
                    structured_assertion=StructuredAssertion(
                        assertion_type="text_present",
                        expected=recipe.expected_text,
                        description=f"Verify text is visible: {recipe.expected_text}",
                    ),
                ),
            ]
        )
        return steps

    if recipe.recipe_type == "text_click_to_text":
        steps.extend(
            [
                FlowStep(
                    step_id=1,
                    action="click",
                    target_text=recipe.trigger_text,
                    description=f"Click visible text: {recipe.trigger_text}",
                ),
                FlowStep(
                    step_id=2,
                    action="wait",
                    value=_wait_value(recipe.wait_before_assert_secs),
                    description="Wait for the target surface to settle",
                ),
                FlowStep(
                    step_id=3,
                    action="assert",
                    value=recipe.expected_text,
                    description=f"Verify text is visible: {recipe.expected_text}",
                    structured_assertion=StructuredAssertion(
                        assertion_type="text_present",
                        expected=recipe.expected_text,
                        description=f"Verify text is visible: {recipe.expected_text}",
                    ),
                ),
            ]
        )
        return steps

    if recipe.recipe_type == "text_then_text_to_text":
        steps.extend(
            [
                FlowStep(
                    step_id=1,
                    action="click",
                    target_text=recipe.trigger_text,
                    description=f"Click visible text: {recipe.trigger_text}",
                ),
                FlowStep(
                    step_id=2,
                    action="wait",
                    value=_wait_value(recipe.intermediate_wait_secs),
                    description="Wait for intermediate view to settle",
                ),
                FlowStep(
                    step_id=3,
                    action="click",
                    target_text=recipe.follow_up_text,
                    description=f"Click visible text: {recipe.follow_up_text}",
                ),
                FlowStep(
                    step_id=4,
                    action="wait",
                    value=_wait_value(recipe.wait_before_assert_secs),
                    description="Wait for target panel to settle",
                ),
                FlowStep(
                    step_id=5,
                    action="assert",
                    value=recipe.expected_text,
                    description=f"Verify text is visible: {recipe.expected_text}",
                    structured_assertion=StructuredAssertion(
                        assertion_type="text_present",
                        expected=recipe.expected_text,
                        description=f"Verify text is visible: {recipe.expected_text}",
                    ),
                ),
            ]
        )
        return steps

    if recipe.recipe_type == "text_then_selector_to_text":
        steps.extend(
            [
                FlowStep(
                    step_id=1,
                    action="click",
                    target_text=recipe.trigger_text,
                    description=f"Click visible text: {recipe.trigger_text}",
                ),
                FlowStep(
                    step_id=2,
                    action="wait",
                    value=_wait_value(recipe.intermediate_wait_secs),
                    description="Wait for intermediate view to settle",
                ),
                FlowStep(
                    step_id=3,
                    action="click",
                    selector=recipe.follow_up_selector,
                    target_text="curated follow-up selector",
                    description="Click curated follow-up selector",
                ),
                FlowStep(
                    step_id=4,
                    action="wait",
                    value=_wait_value(recipe.wait_before_assert_secs),
                    description="Wait for target panel to settle",
                ),
                FlowStep(
                    step_id=5,
                    action="assert",
                    value=recipe.expected_text,
                    description=f"Verify text is visible: {recipe.expected_text}",
                    structured_assertion=StructuredAssertion(
                        assertion_type="text_present",
                        expected=recipe.expected_text,
                        description=f"Verify text is visible: {recipe.expected_text}",
                    ),
                ),
            ]
        )
        return steps

    steps.extend(
        [
            FlowStep(
                step_id=1,
                action="click",
                selector=recipe.trigger_selector,
                target_text="curated entry point",
                description="Click curated baseline entry selector",
            ),
            FlowStep(
                step_id=2,
                action="wait",
                value=_wait_value(recipe.intermediate_wait_secs),
                description="Wait for intermediate dialog or detail surface",
            ),
            FlowStep(
                step_id=3,
                action="click",
                aria_role=recipe.follow_up_role,
                aria_name=recipe.follow_up_name,
                target_text=recipe.follow_up_name,
                description=f"Click {recipe.follow_up_name}",
            ),
            FlowStep(
                step_id=4,
                action="wait",
                value=_wait_value(recipe.wait_before_assert_secs),
                description="Wait for target route to load",
            ),
            FlowStep(
                step_id=5,
                action="assert",
                value=f"URL contains {recipe.expected_url_contains}",
                description=f"Verify URL contains {recipe.expected_url_contains}",
                structured_assertion=StructuredAssertion(
                    assertion_type="url_contains",
                    expected=recipe.expected_url_contains,
                    description=f"Verify URL contains {recipe.expected_url_contains}",
                ),
            ),
        ]
    )
    return steps


async def package_authenticated_saas_baseline(
    app_url: str,
    baseline_name: str,
    recipes: list[dict],
    profile_name: Optional[str] = None,
) -> dict:
    """Create strict-step authenticated SaaS release-gate flows from reusable recipes."""
    url_err = validate_app_url(app_url)
    if url_err:
        return {"error": url_err}
    if not baseline_name or not baseline_name.strip():
        return {"error": "baseline_name is required"}
    if not recipes:
        return {"error": "recipes must include at least one baseline recipe"}

    parsed: list[AuthenticatedBaselineRecipe] = []
    for recipe in recipes:
        parsed.append(AuthenticatedBaselineRecipe.model_validate(recipe))

    created_flows: list[dict] = []
    flow_ids: list[str] = []

    for recipe in parsed:
        steps = _build_steps_for_recipe(app_url, recipe)
        plan = build_execution_plan(
            goal_text=recipe.goal,
            app_url=app_url,
            profile_name=profile_name,
            business_criticality=recipe.business_criticality,
            planning_source="baseline_recipe",
            assertions=[
                step.value or step.description
                for step in steps
                if step.action == "assert" and (step.value or step.description)
            ],
            run_mode="strict_steps",
        )
        flow = build_recorded_flow(
            flow_name=recipe.flow_name,
            app_url=app_url,
            goal=recipe.goal,
            steps=steps,
            assertions_json=[
                step.value or step.description
                for step in steps
                if step.action == "assert" and (step.value or step.description)
            ],
            entry_url=recipe.entry_url or app_url,
            business_criticality=recipe.business_criticality,
            intent_contract=build_intent_contract(plan),
            run_mode_override="strict_steps",
        )
        await sqlite.save_flow(flow)
        flow_ids.append(flow.flow_id)
        created_flows.append(
            {
                "flow_id": flow.flow_id,
                "flow_name": flow.flow_name,
                "recipe_type": recipe.recipe_type,
                "business_criticality": recipe.business_criticality,
                "entry_url": recipe.entry_url or app_url,
                "run_mode_override": "strict_steps",
            }
        )

    return {
        "baseline_name": baseline_name,
        "app_url": app_url,
        "profile_name": profile_name,
        "status": "packaged",
        "flow_count": len(created_flows),
        "release_gate_flow_ids": flow_ids,
        "created_flows": created_flows,
        "recommended_release_check": {
            "tool": "run_release_check",
            "arguments": {
                "app_url": app_url,
                "flow_ids": flow_ids,
                "profile_name": profile_name,
                "mode": "replay",
            },
        },
        "workflow_hint": (
            f"Authenticated SaaS baseline '{baseline_name}' packaged with {len(created_flows)} strict-step flows. "
            f"Next: run_release_check(app_url='{app_url}', flow_ids={flow_ids}, profile_name={profile_name!r}, mode='replay')."
        ),
    }
