"""Tests for engine/recommendations.py."""

from __future__ import annotations

from blop.schemas import (
    CoverageGap,
    FlakinessSignal,
    PyramidHealthSummary,
    QAContext,
    RecommendationSet,
    RiskMatrixEntry,
)


def _minimal_pyramid(
    happy_path_count: int = 0,
    negative_path_count: int = 1,
    total_flows: int = 1,
    is_bottom_heavy: bool = False,
    coverage_efficiency: float = 1.0,
) -> PyramidHealthSummary:
    return PyramidHealthSummary(
        happy_path_count=happy_path_count,
        negative_path_count=negative_path_count,
        edge_case_count=0,
        total_flows=total_flows,
        is_bottom_heavy=is_bottom_heavy,
        coverage_efficiency=coverage_efficiency,
    )


def _make_qa_context(
    risk_matrix: list[RiskMatrixEntry] | None = None,
    coverage_gaps: list[CoverageGap] | None = None,
    flakiness_signals: list[FlakinessSignal] | None = None,
    pyramid: PyramidHealthSummary | None = None,
    defect_distribution: dict[str, int] | None = None,
) -> QAContext:
    return QAContext(
        app_url="https://example.com",
        test_pyramid=pyramid or _minimal_pyramid(),
        coverage_gaps=coverage_gaps or [],
        flakiness_signals=flakiness_signals or [],
        defect_distribution=defect_distribution or {},
        risk_matrix=risk_matrix or [],
        analyzed_flows=1,
        analyzed_runs=5,
    )


def test_blocker_for_critical_flow_failure():
    from blop.engine.recommendations import generate_recommendations

    risk_matrix = [
        RiskMatrixEntry(
            flow_name="checkout",
            likelihood=0.8,
            impact=1.0,
            risk_score=0.8,
            business_criticality="revenue",
        )
    ]
    ctx = _make_qa_context(risk_matrix=risk_matrix)

    result = generate_recommendations(ctx)

    assert isinstance(result, RecommendationSet)
    assert len(result.blockers) > 0
    assert result.blockers[0].category == "BLOCKER"
    assert "revenue" in result.blockers[0].rationale.lower()


def test_high_risk_for_coverage_gap():
    from blop.engine.recommendations import generate_recommendations

    gaps = [
        CoverageGap(
            flow_name="signup",
            gap_type="no_test",
            severity="critical",
            business_criticality="activation",
        )
    ]
    ctx = _make_qa_context(coverage_gaps=gaps)

    result = generate_recommendations(ctx)

    assert len(result.high_risk_gaps) > 0
    assert result.high_risk_gaps[0].category == "HIGH_RISK"


def test_maintenance_alert_for_flaky_flow():
    from blop.engine.recommendations import generate_recommendations

    signals = [
        FlakinessSignal(
            flow_name="login",
            pass_rate=0.5,
            cv=0.45,
            run_count=10,
            is_flaky=True,
        )
    ]
    ctx = _make_qa_context(flakiness_signals=signals)

    result = generate_recommendations(ctx)

    assert len(result.maintenance_alerts) > 0
    assert result.maintenance_alerts[0].category == "MAINTENANCE"


def test_optimization_for_bottom_heavy_pyramid():
    from blop.engine.recommendations import generate_recommendations

    pyramid = _minimal_pyramid(
        happy_path_count=9,
        negative_path_count=1,
        total_flows=10,
        is_bottom_heavy=True,
        coverage_efficiency=0.1,
    )
    ctx = _make_qa_context(pyramid=pyramid)

    result = generate_recommendations(ctx)

    assert len(result.optimizations) > 0
    rationale = result.optimizations[0].rationale.lower()
    assert "pyramid" in rationale or "negative" in rationale


def test_summary_is_non_empty():
    from blop.engine.recommendations import generate_recommendations

    signals = [
        FlakinessSignal(
            flow_name="dashboard",
            pass_rate=0.6,
            cv=0.4,
            run_count=8,
            is_flaky=True,
        )
    ]
    ctx = _make_qa_context(flakiness_signals=signals)

    result = generate_recommendations(ctx)

    assert isinstance(result.summary, str)
    assert len(result.summary) > 20
