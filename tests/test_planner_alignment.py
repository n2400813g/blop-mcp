from __future__ import annotations

from blop.engine.planner import build_execution_plan, build_intent_contract


def test_build_execution_plan_for_authenticated_editor_goal():
    plan = build_execution_plan(
        goal_text="Open a project in the editor and verify the captions panel is visible.",
        app_url="https://app.example.com",
        command="record the logged in editor flow",
        profile_name="team_login",
        business_criticality="activation",
        planning_source="nl_command",
    )

    assert plan.effective_auth_expectation == "authenticated"
    assert plan.target_surface == "editor"
    assert plan.intended_replay_mode == "strict_steps"
    assert "/editor" in plan.expected_landing_url_patterns


def test_build_execution_plan_for_public_discovery_goal():
    plan = build_execution_plan(
        goal_text="Discover public marketing journeys",
        app_url="https://example.com",
        command="discover public flows",
        planning_source="nl_command",
    )

    assert plan.intent == "discover"
    assert plan.effective_auth_expectation == "anonymous"
    assert plan.target_surface == "public_site"


def test_build_intent_contract_blocks_goal_fallback_by_default():
    plan = build_execution_plan(
        goal_text="Complete checkout",
        app_url="https://example.com",
        business_criticality="revenue",
    )
    contract = build_intent_contract(plan)

    assert "goal_fallback_without_surface_match" in contract.forbidden_shortcuts
    assert "hybrid_repair" in contract.allowed_fallbacks


def test_build_execution_plan_anchors_same_domain_public_goal_to_explicit_url():
    plan = build_execution_plan(
        goal_text="Navigate to https://testpages.eviltester.com/pages/input-elements/text-inputs/ and verify the text inputs are usable.",
        app_url="https://testpages.eviltester.com/",
        business_criticality="activation",
    )

    assert plan.effective_auth_expectation == "anonymous"
    assert plan.target_surface == "public_site"
    assert plan.expected_landing_url_patterns[0] == "https://testpages.eviltester.com/pages/input-elements/text-inputs/"
