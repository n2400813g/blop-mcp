# tests/test_schema_hardening.py
import inspect

from blop.mcp.envelope import ToolError


def test_tool_error_has_all_required_fields():
    fields = ToolError.model_fields
    for f in ["code", "message", "likely_cause", "suggested_fix", "retry_safe", "stage", "details"]:
        assert f in fields, f"ToolError missing field: {f}"


def test_run_regression_test_mode_has_default_replay():
    import blop.tools.regression as mod

    # Find the callable with a 'mode' parameter
    for name in dir(mod):
        obj = getattr(mod, name)
        if callable(obj) and not name.startswith("_"):
            try:
                sig = inspect.signature(obj)
                if "mode" in sig.parameters:
                    assert sig.parameters["mode"].default == "replay", f"{name}.mode default must be 'replay'"
            except (ValueError, TypeError):
                pass


def test_business_criticality_valid_values():
    """Any tool accepting business_criticality must only allow the 5 canonical values."""
    valid = {"revenue", "activation", "retention", "support", "other"}
    import blop.tools.journeys as mod

    for name in dir(mod):
        obj = getattr(mod, name)
        if callable(obj) and not name.startswith("_"):
            try:
                sig = inspect.signature(obj)
                if "business_criticality" in sig.parameters:
                    ann = sig.parameters["business_criticality"].annotation
                    ann_str = str(ann)
                    for v in valid:
                        assert v in ann_str, f"business_criticality annotation missing '{v}': {ann_str}"
            except (ValueError, TypeError):
                pass
