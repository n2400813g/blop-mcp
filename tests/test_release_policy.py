"""Unit tests for Policy-Aware Release Gates (BLO-74 / BLO-75 / BLO-76 / BLO-77).

These tests do not require a running app, browser, or Appium server.
"""

from __future__ import annotations

import uuid

import pytest

from blop.reporting.results import _compute_release_recommendation
from blop.schemas import (
    DEFAULT_RELEASE_POLICY,
    CriticalityGate,
    FailureCase,
    PolicyEvaluation,
    PolicyGateResult,
    ReleasePolicy,
)

# ── Helpers ───────────────────────────────────────────────────────────────────


def _make_case(
    status="pass",
    severity="none",
    criticality="other",
    failure_class=None,
) -> FailureCase:
    return FailureCase(
        run_id=uuid.uuid4().hex,
        flow_id=uuid.uuid4().hex,
        flow_name="test flow",
        status=status,
        severity=severity,
        business_criticality=criticality,
        failure_class=failure_class,
    )


# ── Schema tests (BLO-74) ─────────────────────────────────────────────────────


class TestReleasePolicySchema:
    def test_default_policy_is_valid(self):
        p = DEFAULT_RELEASE_POLICY
        assert p.policy_id == "default"
        assert p.is_default is True
        assert len(p.gates) == 5

    def test_gate_for_returns_correct_gate(self):
        p = DEFAULT_RELEASE_POLICY
        gate = p.gate_for("revenue")
        assert gate is not None
        assert gate.on_failure == "BLOCK"
        assert gate.enabled is True

    def test_gate_for_returns_none_for_unknown(self):
        assert DEFAULT_RELEASE_POLICY.gate_for("nonexistent") is None

    def test_criticality_gate_defaults(self):
        g = CriticalityGate(criticality="revenue")
        assert g.on_failure == "INVESTIGATE"
        assert g.min_failures == 1
        assert g.enabled is True

    def test_policy_round_trip(self):
        p = ReleasePolicy(
            policy_name="Test Policy",
            gates=[CriticalityGate(criticality="revenue", on_failure="BLOCK")],
            block_on_any_failure=True,
        )
        dumped = p.model_dump_json()
        loaded = ReleasePolicy.model_validate_json(dumped)
        assert loaded.policy_name == "Test Policy"
        assert loaded.block_on_any_failure is True
        assert loaded.gates[0].criticality == "revenue"

    def test_policy_gate_result_schema(self):
        gr = PolicyGateResult(
            criticality="revenue",
            gate_enabled=True,
            failures_found=2,
            threshold=1,
            fired=True,
            decision_contribution="BLOCK",
            rationale="2 revenue failure(s) — gate fires BLOCK.",
        )
        assert gr.fired is True
        assert gr.decision_contribution == "BLOCK"

    def test_policy_evaluation_schema(self):
        pe = PolicyEvaluation(
            policy_id="default",
            policy_name="Default Policy",
            gate_results=[],
            final_decision="BLOCK",
            contributing_gates=["revenue"],
            applied_global_flags=[],
            rationale="1 revenue failure(s) trigger BLOCK gate.",
        )
        assert pe.final_decision == "BLOCK"
        assert "revenue" in pe.contributing_gates


# ── Gate evaluation tests (BLO-75) ───────────────────────────────────────────


class TestComputeReleaseRecommendation:
    def test_ship_when_all_pass(self):
        cases = [_make_case("pass") for _ in range(3)]
        rec = _compute_release_recommendation(cases, "completed")
        assert rec["decision"] == "SHIP"
        assert rec["blocker_count"] == 0

    def test_block_on_revenue_failure_via_default_policy(self):
        cases = [_make_case("fail", criticality="revenue")]
        rec = _compute_release_recommendation(cases, "completed")
        assert rec["decision"] == "BLOCK"
        assert "gate:revenue" in rec.get("policy_gates_applied", [])

    def test_block_on_activation_failure_via_default_policy(self):
        cases = [_make_case("fail", criticality="activation")]
        rec = _compute_release_recommendation(cases, "completed")
        assert rec["decision"] == "BLOCK"

    def test_investigate_on_retention_failure_via_default_policy(self):
        cases = [_make_case("fail", criticality="retention")]
        rec = _compute_release_recommendation(cases, "completed")
        assert rec["decision"] == "INVESTIGATE"

    def test_investigate_on_other_failure_no_active_gate(self):
        # "other" gate is disabled in DEFAULT_RELEASE_POLICY
        cases = [_make_case("fail", criticality="other")]
        rec = _compute_release_recommendation(cases, "completed")
        # Ungated failure escalates SHIP → INVESTIGATE (no gate should have fired)
        assert rec["decision"] == "INVESTIGATE"
        fired_gates = [g for g in rec.get("gate_results", []) if g.get("fired")]
        assert not fired_gates, f"No gate should fire for ungated 'other' failures, got: {fired_gates}"

    def test_blocker_severity_always_blocks_regardless_of_criticality(self):
        cases = [_make_case("fail", severity="blocker", criticality="other")]
        rec = _compute_release_recommendation(cases, "completed")
        assert rec["decision"] == "BLOCK"
        assert rec["blocker_count"] == 1

    def test_custom_policy_block_on_any_failure(self):
        policy = ReleasePolicy(
            policy_name="Strict",
            gates=[],
            block_on_any_failure=True,
        )
        cases = [_make_case("fail", criticality="support")]
        rec = _compute_release_recommendation(cases, "completed", policy=policy)
        assert rec["decision"] == "BLOCK"
        assert "block_on_any_failure" in rec.get("policy_gates_applied", [])

    def test_custom_policy_ignore_gate(self):
        policy = ReleasePolicy(
            policy_name="Lenient",
            gates=[CriticalityGate(criticality="revenue", on_failure="IGNORE", enabled=True)],
        )
        cases = [_make_case("fail", criticality="revenue")]
        rec = _compute_release_recommendation(cases, "completed", policy=policy)
        # IGNORE means revenue failures don't affect decision — ungated escalates to INVESTIGATE
        assert rec["decision"] in ("SHIP", "INVESTIGATE")
        assert rec["decision"] != "BLOCK"

    def test_custom_policy_min_failures_threshold(self):
        policy = ReleasePolicy(
            policy_name="Threshold",
            gates=[CriticalityGate(criticality="revenue", on_failure="BLOCK", min_failures=3, enabled=True)],
        )
        # Only 2 revenue failures — gate should NOT fire
        cases = [_make_case("fail", criticality="revenue") for _ in range(2)]
        rec = _compute_release_recommendation(cases, "completed", policy=policy)
        assert rec["decision"] != "BLOCK"

        # 3 failures — gate fires
        cases3 = [_make_case("fail", criticality="revenue") for _ in range(3)]
        rec3 = _compute_release_recommendation(cases3, "completed", policy=policy)
        assert rec3["decision"] == "BLOCK"

    def test_disabled_gate_does_not_fire(self):
        policy = ReleasePolicy(
            policy_name="Disabled",
            gates=[CriticalityGate(criticality="revenue", on_failure="BLOCK", enabled=False)],
        )
        cases = [_make_case("fail", criticality="revenue")]
        rec = _compute_release_recommendation(cases, "completed", policy=policy)
        # Disabled gate + ungated escalation → INVESTIGATE, not BLOCK
        assert rec["decision"] == "INVESTIGATE"


# ── BLO-76: Structured gate output ───────────────────────────────────────────


class TestStructuredGateOutput:
    def test_gate_results_present_in_output(self):
        cases = [_make_case("fail", criticality="revenue")]
        rec = _compute_release_recommendation(cases, "completed")
        assert "gate_results" in rec
        assert isinstance(rec["gate_results"], list)
        assert len(rec["gate_results"]) == len(DEFAULT_RELEASE_POLICY.gates)

    def test_fired_gate_result_has_correct_fields(self):
        cases = [_make_case("fail", criticality="revenue")]
        rec = _compute_release_recommendation(cases, "completed")
        revenue_gate = next(g for g in rec["gate_results"] if g["criticality"] == "revenue")
        assert revenue_gate["fired"] is True
        assert revenue_gate["failures_found"] == 1
        assert revenue_gate["decision_contribution"] == "BLOCK"

    def test_unfired_gate_result_has_zero_failures(self):
        cases = [_make_case("pass")]
        rec = _compute_release_recommendation(cases, "completed")
        for gr in rec["gate_results"]:
            assert gr["fired"] is False
            assert gr["failures_found"] == 0

    def test_active_gating_policy_block_present(self):
        cases = [_make_case("fail", criticality="revenue")]
        rec = _compute_release_recommendation(cases, "completed")
        agp = rec.get("active_gating_policy", {})
        assert agp["policy_id"] == "default"
        assert "revenue" in agp["contributing_gates"]

    def test_active_gating_policy_empty_when_ship(self):
        cases = [_make_case("pass")]
        rec = _compute_release_recommendation(cases, "completed")
        agp = rec.get("active_gating_policy", {})
        assert agp["contributing_gates"] == []
        assert agp["applied_global_flags"] == []


# ── BLO-77: Stability bucket integration ─────────────────────────────────────


class TestStabilityBucketIntegration:
    def test_install_failure_blocks_when_policy_enabled(self):
        cases = [_make_case("fail", criticality="other")]  # would normally be INVESTIGATE
        rec = _compute_release_recommendation(
            cases,
            "completed",
            stability_bucket="install_or_upgrade_failure",
        )
        assert rec["decision"] == "BLOCK"
        assert "block_on_install_failure" in rec.get("policy_gates_applied", [])

    def test_install_failure_does_not_block_when_policy_disabled(self):
        policy = ReleasePolicy(
            policy_name="No Install Block",
            gates=DEFAULT_RELEASE_POLICY.gates,
            block_on_install_failure=False,
        )
        cases = [_make_case("fail", criticality="other")]
        rec = _compute_release_recommendation(
            cases,
            "completed",
            policy=policy,
            stability_bucket="install_or_upgrade_failure",
        )
        assert rec["decision"] == "INVESTIGATE"

    def test_unknown_stability_blocks_when_policy_enabled(self):
        policy = ReleasePolicy(
            policy_name="Strict Unknown",
            gates=DEFAULT_RELEASE_POLICY.gates,
            block_on_unknown_stability=True,
        )
        cases = [_make_case("fail", criticality="other")]
        rec = _compute_release_recommendation(
            cases,
            "completed",
            policy=policy,
            stability_bucket="unknown_unclassified",
        )
        assert rec["decision"] == "BLOCK"

    def test_unknown_stability_does_not_block_when_policy_disabled(self):
        cases = [_make_case("fail", criticality="other")]
        rec = _compute_release_recommendation(
            cases,
            "completed",
            stability_bucket="unknown_unclassified",
        )
        # block_on_unknown_stability=False in DEFAULT_RELEASE_POLICY
        assert rec["decision"] == "INVESTIGATE"

    def test_auth_failure_bucket_escalates_ship_to_investigate(self):
        # No actual failures, but auth bucket indicates something is wrong
        cases = [_make_case("fail", criticality="other")]
        rec = _compute_release_recommendation(
            cases,
            "completed",
            stability_bucket="auth_session_failure",
        )
        assert rec["decision"] in ("INVESTIGATE", "BLOCK")

    def test_stability_bucket_not_applied_when_none(self):
        cases = [_make_case("pass")]
        rec = _compute_release_recommendation(cases, "completed", stability_bucket=None)
        assert rec["decision"] == "SHIP"
        agp = rec.get("active_gating_policy", {})
        assert agp.get("stability_bucket_applied") is None


# ── SQLite round-trip (BLO-74 storage) ───────────────────────────────────────


@pytest.mark.asyncio
async def test_policy_sqlite_round_trip(tmp_path, monkeypatch):
    """save_policy / get_policy / get_default_policy persists and retrieves correctly."""
    db_path = str(tmp_path / "test_policy.db")
    monkeypatch.setenv("BLOP_DB_PATH", db_path)

    from blop.storage.sqlite import get_default_policy, get_policy, init_db, save_policy

    await init_db()

    policy = ReleasePolicy(
        policy_name="CI Gate",
        description="Block on any revenue/activation failure.",
        is_default=True,
        gates=[
            CriticalityGate(criticality="revenue", on_failure="BLOCK"),
            CriticalityGate(criticality="activation", on_failure="BLOCK"),
        ],
        block_on_any_failure=False,
        block_on_install_failure=True,
    )

    await save_policy(policy)

    # Retrieve by ID
    loaded = await get_policy(policy.policy_id)
    assert loaded is not None
    assert loaded.policy_name == "CI Gate"
    assert len(loaded.gates) == 2
    assert loaded.is_default is True

    # Retrieve as default
    default = await get_default_policy()
    assert default is not None
    assert default.policy_id == policy.policy_id

    # Save a second policy with is_default=True — first should no longer be default
    policy2 = ReleasePolicy(
        policy_name="Lenient Gate",
        is_default=True,
        gates=[],
    )
    await save_policy(policy2)

    default2 = await get_default_policy()
    assert default2 is not None
    assert default2.policy_id == policy2.policy_id

    # Old policy is still retrievable
    old = await get_policy(policy.policy_id)
    assert old is not None


@pytest.mark.asyncio
async def test_get_default_policy_returns_none_when_empty(tmp_path, monkeypatch):
    db_path = str(tmp_path / "empty.db")
    monkeypatch.setenv("BLOP_DB_PATH", db_path)

    from blop.storage.sqlite import get_default_policy, init_db

    await init_db()
    result = await get_default_policy()
    assert result is None
