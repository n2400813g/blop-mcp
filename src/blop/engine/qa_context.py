"""QA intelligence: risk profile from recorded flows and run history."""

from __future__ import annotations

import statistics
from datetime import datetime, timezone

from blop.engine.defect_classifier import categorize_failure_reason
from blop.schemas import (
    CoverageGap,
    FlakinessSignal,
    PyramidHealthSummary,
    QAContext,
    RiskMatrixEntry,
)

_CRITICALITY_IMPACT: dict[str, float] = {
    "revenue": 1.0,
    "activation": 0.8,
    "retention": 0.6,
    "support": 0.4,
    "other": 0.2,
}


def _build_test_pyramid(flows: list[dict]) -> PyramidHealthSummary:
    total = len(flows)
    if total == 0:
        return PyramidHealthSummary(
            happy_path_count=0,
            negative_path_count=0,
            edge_case_count=0,
            total_flows=0,
            is_bottom_heavy=False,
            coverage_efficiency=0.0,
        )

    happy = sum(1 for f in flows if (f.get("assertion_count") or 0) == 0)
    with_assertions = total - happy

    is_bottom_heavy = (happy / total) > 0.8 if total > 0 else False
    coverage_efficiency = with_assertions / total if total > 0 else 0.0

    return PyramidHealthSummary(
        happy_path_count=happy,
        negative_path_count=with_assertions,
        edge_case_count=0,
        total_flows=total,
        is_bottom_heavy=is_bottom_heavy,
        coverage_efficiency=coverage_efficiency,
    )


def _severity_for_criticality(criticality: str | None) -> str:
    mapping = {
        "revenue": "critical",
        "activation": "critical",
        "retention": "high",
        "support": "medium",
        "other": "low",
    }
    return mapping.get(criticality or "other", "low")


def _build_coverage_gaps(
    flows: list[dict],
    run_cases: list[dict],
    now: datetime,
) -> list[CoverageGap]:
    gaps: list[CoverageGap] = []
    cases_by_flow: dict[str, list[dict]] = {}
    for case in run_cases:
        name = case.get("flow_name", "")
        cases_by_flow.setdefault(name, []).append(case)

    for flow in flows:
        flow_name = flow.get("flow_name", "")
        criticality = flow.get("business_criticality", "other")
        assertion_count = flow.get("assertion_count") or 0
        cases = cases_by_flow.get(flow_name, [])

        if not cases:
            gaps.append(
                CoverageGap(
                    flow_name=flow_name,
                    gap_type="no_test",
                    severity=_severity_for_criticality(criticality),
                    business_criticality=criticality,
                    last_run_days_ago=None,
                )
            )
            continue

        if assertion_count == 0:
            gaps.append(
                CoverageGap(
                    flow_name=flow_name,
                    gap_type="unasserted",
                    severity="low",
                    business_criticality=criticality,
                    last_run_days_ago=None,
                )
            )

        all_no_failure = all(not case.get("failure_reason") for case in cases)
        if all_no_failure and assertion_count > 0:
            gaps.append(
                CoverageGap(
                    flow_name=flow_name,
                    gap_type="happy_path_only",
                    severity="medium",
                    business_criticality=criticality,
                    last_run_days_ago=None,
                )
            )

        try:
            most_recent_str = max((c.get("created_at") or "") for c in cases if c.get("created_at"))
            if most_recent_str:
                most_recent = datetime.fromisoformat(most_recent_str.replace("Z", "+00:00"))
                if most_recent.tzinfo is None:
                    most_recent = most_recent.replace(tzinfo=timezone.utc)
                delta = now - most_recent
                days_ago = delta.days
                if days_ago > 30:
                    gaps.append(
                        CoverageGap(
                            flow_name=flow_name,
                            gap_type="stale",
                            severity="medium",
                            business_criticality=criticality,
                            last_run_days_ago=days_ago,
                        )
                    )
        except (ValueError, TypeError):
            pass

    return gaps


def _build_flakiness_signals(
    flows: list[dict],
    run_cases: list[dict],
) -> list[FlakinessSignal]:
    cases_by_flow: dict[str, list[dict]] = {}
    for case in run_cases:
        name = case.get("flow_name", "")
        cases_by_flow.setdefault(name, []).append(case)

    signals: list[FlakinessSignal] = []
    seen: set[str] = set()

    for flow in flows:
        flow_name = flow.get("flow_name", "")
        if flow_name in seen:
            continue
        seen.add(flow_name)

        cases = cases_by_flow.get(flow_name, [])
        if not cases:
            continue

        results = [1.0 if c.get("status") == "pass" else 0.0 for c in cases]
        run_count = len(results)
        pass_rate = sum(results) / run_count if run_count > 0 else 0.0

        if run_count < 3:
            signals.append(
                FlakinessSignal(
                    flow_name=flow_name,
                    pass_rate=pass_rate,
                    cv=0.0,
                    run_count=run_count,
                    is_flaky=False,
                )
            )
            continue

        mean_val = statistics.mean(results)
        if mean_val == 0.0:
            cv = 0.0
            is_flaky = False
        elif mean_val == 1.0:
            cv = 0.0
            is_flaky = False
        else:
            stdev_val = statistics.stdev(results)
            cv = stdev_val / mean_val
            is_flaky = cv > 0.3

        signals.append(
            FlakinessSignal(
                flow_name=flow_name,
                pass_rate=pass_rate,
                cv=cv,
                run_count=run_count,
                is_flaky=is_flaky,
            )
        )

    return signals


def _build_risk_matrix(
    flows: list[dict],
    run_cases: list[dict],
) -> list[RiskMatrixEntry]:
    cases_by_flow: dict[str, list[dict]] = {}
    for case in run_cases:
        name = case.get("flow_name", "")
        cases_by_flow.setdefault(name, []).append(case)

    entries: list[RiskMatrixEntry] = []

    for flow in flows:
        flow_name = flow.get("flow_name", "")
        criticality = flow.get("business_criticality", "other")
        impact = _CRITICALITY_IMPACT.get(criticality, 0.2)

        cases = cases_by_flow.get(flow_name, [])
        if not cases:
            likelihood = 0.3
        else:
            fail_count = sum(1 for c in cases if c.get("status") in ("fail", "error", "blocked"))
            likelihood = fail_count / len(cases)

        risk_score = round(likelihood * impact, 4)

        entries.append(
            RiskMatrixEntry(
                flow_name=flow_name,
                likelihood=likelihood,
                impact=impact,
                risk_score=risk_score,
                business_criticality=criticality,
            )
        )

    return entries


def _build_defect_distribution(run_cases: list[dict]) -> dict[str, int]:
    dist: dict[str, int] = {
        "functional": 0,
        "performance": 0,
        "ui": 0,
        "integration": 0,
        "security": 0,
    }
    for case in run_cases:
        if case.get("status") not in ("fail", "error", "blocked"):
            continue
        reason = case.get("failure_reason")
        category = categorize_failure_reason(reason)
        dist[category] = dist.get(category, 0) + 1

    return {k: v for k, v in dist.items() if v > 0}


async def build_qa_context(
    app_url: str,
    flows: list[dict],
    run_cases: list[dict],
    lookback_runs: int = 10,
) -> QAContext:
    """Build QAContext from recorded flow dicts and run-case dicts."""
    _ = lookback_runs
    now = datetime.now(timezone.utc)

    test_pyramid = _build_test_pyramid(flows)
    coverage_gaps = _build_coverage_gaps(flows, run_cases, now)
    flakiness_signals = _build_flakiness_signals(flows, run_cases)
    defect_distribution = _build_defect_distribution(run_cases)
    risk_matrix = _build_risk_matrix(flows, run_cases)

    unique_run_ids = {c.get("run_id") for c in run_cases if c.get("run_id")}
    analyzed_runs = len(unique_run_ids) if unique_run_ids else len(run_cases)

    return QAContext(
        app_url=app_url,
        test_pyramid=test_pyramid,
        coverage_gaps=coverage_gaps,
        flakiness_signals=flakiness_signals,
        defect_distribution=defect_distribution,
        risk_matrix=risk_matrix,
        analyzed_flows=len(flows),
        analyzed_runs=analyzed_runs,
    )
