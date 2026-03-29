"""Deterministic QA recommendations from QAContext."""

from __future__ import annotations

from datetime import datetime, timezone

from blop.schemas import QAContext, Recommendation, RecommendationSet


def generate_recommendations(qa_context: QAContext) -> RecommendationSet:
    """Produce prioritized recommendations using risk-based testing and pyramid heuristics."""
    blockers: list[Recommendation] = []
    high_risk_gaps: list[Recommendation] = []
    maintenance_alerts: list[Recommendation] = []
    optimizations: list[Recommendation] = []
    seen_blocker_flows: set[str] = set()

    for entry in qa_context.risk_matrix:
        if entry.risk_score >= 0.7 and entry.business_criticality == "revenue":
            if entry.flow_name in seen_blocker_flows:
                continue
            seen_blocker_flows.add(entry.flow_name)
            blockers.append(
                Recommendation(
                    category="BLOCKER",
                    title=f"Resolve elevated risk in revenue flow '{entry.flow_name}'",
                    rationale=(
                        "Risk-based testing (ISO 29119-1): revenue journeys combine maximum business "
                        "impact with observed failure likelihood; ship decisions require this risk to "
                        "be addressed or explicitly accepted."
                    ),
                    evidence=[
                        f"flow={entry.flow_name}",
                        f"risk_score={entry.risk_score}",
                        f"likelihood={entry.likelihood}",
                        f"impact={entry.impact}",
                    ],
                    remediation_steps=[
                        f"Reproduce and debug '{entry.flow_name}' (e.g. debug_test_case or strict replay).",
                        "Tighten assertions on the failing transition and capture network/console evidence.",
                        "Re-run release replay for revenue and activation flows before ship.",
                    ],
                    confidence="HIGH",
                )
            )

    for gap in qa_context.coverage_gaps:
        if gap.severity == "critical":
            high_risk_gaps.append(
                Recommendation(
                    category="HIGH_RISK",
                    title=f"Add regression coverage for '{gap.flow_name}'",
                    rationale=(
                        "Critical business-criticality flows without execution history create defect escape risk: "
                        "changes can ship without any automated signal on the journey."
                    ),
                    evidence=[f"gap_type={gap.gap_type}", f"criticality={gap.business_criticality or 'unknown'}"],
                    remediation_steps=[
                        f"Record or import a journey for '{gap.flow_name}' and tag business_criticality.",
                        "Include at least one outcome assertion or structured check.",
                        "Add the flow to the default release gate set (revenue/activation filter).",
                    ],
                    confidence="HIGH" if gap.gap_type == "no_test" else "MEDIUM",
                )
            )

    for sig in qa_context.flakiness_signals:
        if sig.is_flaky:
            maintenance_alerts.append(
                Recommendation(
                    category="MAINTENANCE",
                    title=f"Stabilize flaky journey '{sig.flow_name}'",
                    rationale=(
                        "Flaky tests erode CI signal quality (GQM): high variance in pass/fail outcomes "
                        "masks real regressions and slows release decisions."
                    ),
                    evidence=[
                        f"cv={round(sig.cv, 4)}",
                        f"pass_rate={round(sig.pass_rate, 4)}",
                        f"run_count={sig.run_count}",
                    ],
                    remediation_steps=[
                        "Inspect step_failure_index history and stabilize selectors or waits.",
                        "Split environment noise from product bugs using debug_test_case evidence.",
                        "Quarantine only as a last resort — prefer fixing root cause.",
                    ],
                    confidence="MEDIUM" if sig.run_count >= 5 else "LOW",
                )
            )

    if qa_context.test_pyramid.is_bottom_heavy:
        optimizations.append(
            Recommendation(
                category="OPTIMIZATION",
                title="Rebalance the test pyramid toward asserted journeys",
                rationale=(
                    "Test pyramid guidance (Cohn): when most journeys lack assertions, confidence is "
                    "skewed toward 'can navigate' rather than 'correct outcomes and failure handling'."
                ),
                evidence=[
                    f"happy_path_ratio={qa_context.test_pyramid.happy_path_count}/{qa_context.test_pyramid.total_flows}",
                    f"coverage_efficiency={round(qa_context.test_pyramid.coverage_efficiency, 4)}",
                ],
                remediation_steps=[
                    "Add negative-path and assertion coverage for top revenue/activation flows.",
                    "Promote critical checks from implicit UI navigation to explicit assertions.",
                ],
                confidence="MEDIUM",
            )
        )

    parts = []
    if blockers:
        parts.append(f"{len(blockers)} blocker(s) tied to revenue risk scores.")
    if high_risk_gaps:
        parts.append(f"{len(high_risk_gaps)} high-risk coverage gap(s) on critical journeys.")
    if maintenance_alerts:
        parts.append(f"{len(maintenance_alerts)} maintenance alert(s) for flaky flows.")
    if optimizations:
        parts.append("Test pyramid is bottom-heavy; add asserted and negative-path coverage.")
    if not parts:
        parts.append(
            "No automated blocker signals from the current history; continue monitoring run health and coverage."
        )
    summary = " ".join(parts)

    return RecommendationSet(
        app_url=qa_context.app_url,
        summary=summary,
        blockers=blockers,
        high_risk_gaps=high_risk_gaps,
        maintenance_alerts=maintenance_alerts,
        optimizations=optimizations,
        generated_at=datetime.now(timezone.utc).isoformat(),
    )
