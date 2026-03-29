"""Process insights from run health events (optional PM4Py)."""

from __future__ import annotations

from typing import Any

from blop.engine.errors import BLOP_PM4PY_INSIGHTS_FAILED, BlopError
from blop.reporting.process_event_log import build_process_event_log_for_run, event_log_to_csv_dicts


async def get_process_insights(run_id: str, include_pm4py: bool = True) -> dict[str, Any]:
    """Summarize process variants from the derived event log; optional PM4Py stats."""
    rows = await build_process_event_log_for_run(run_id, limit=2000)
    flat = event_log_to_csv_dicts(rows)
    if not flat:
        return {
            "run_id": run_id,
            "event_count": 0,
            "variants": [],
            "note": "No health events found for this run.",
        }

    # Simple variant discovery without PM4Py: group by case → activity sequence
    by_case: dict[str, list[str]] = {}
    for d in flat:
        cid = str(d.get("case:concept:name") or "")
        act = str(d.get("concept:name") or "")
        by_case.setdefault(cid, []).append(act)

    variant_counts: dict[str, int] = {}
    case_to_variant: dict[str, str] = {}
    for cid, acts in by_case.items():
        key = " → ".join(acts)
        variant_counts[key] = variant_counts.get(key, 0) + 1
        case_to_variant[cid] = key

    ranked = sorted(variant_counts.items(), key=lambda x: -x[1])[:25]

    out: dict[str, Any] = {
        "run_id": run_id,
        "event_count": len(flat),
        "case_count": len(by_case),
        "unique_variants": len(variant_counts),
        "top_variants": [{"variant": k, "case_count": v} for k, v in ranked],
    }

    if not include_pm4py:
        out["pm4py"] = None
        return out

    try:
        import pandas as pd  # type: ignore[import-untyped]
        import pm4py  # type: ignore[import-untyped]

        df = pd.DataFrame(flat)
        df = pm4py.format_dataframe(
            df,
            case_id="case:concept:name",
            activity_key="concept:name",
            timestamp_key="time:timestamp",
        )
        log = pm4py.convert_to_event_log(df)
        from pm4py.statistics.variants.log import get as variants_get  # type: ignore[import-untyped]

        variants = variants_get.get_variants(log)
        top = sorted(variants.items(), key=lambda x: -len(x[1]))[:15]
        out["pm4py"] = {
            "available": True,
            "variant_count": len(variants),
            "top_variants_pm4py": [
                {"activities": list(variant_key), "trace_count": len(traces)} for variant_key, traces in top
            ],
        }
    except ImportError:
        out["pm4py"] = {
            "available": False,
            "hint": "pip install 'blop-mcp[insights]' (pm4py + pandas)",
        }
    except Exception as e:
        _msg = str(e)[:500]
        _be = BlopError(
            BLOP_PM4PY_INSIGHTS_FAILED,
            _msg,
            details={"cause": type(e).__name__},
        ).to_dict()["error"]
        out["pm4py"] = {"available": False, "error": _msg, "blop_error": _be}

    return out


async def export_run_trace_otel(run_id: str) -> dict[str, Any]:
    """OTLP-shaped JSON for enterprise pipelines (no network)."""
    from blop.reporting.otel_export import build_otel_run_trace_export

    return await build_otel_run_trace_export(run_id)
