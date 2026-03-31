# tests/test_envelope_schema.py
from blop.mcp.envelope import ToolError, err_response, finalize_tool_payload


def test_tool_error_has_all_diagnostic_fields():
    err = ToolError(
        code="BLOP_AUTH_PROFILE_NOT_FOUND",
        message="Profile not found",
        likely_cause="Profile was never created",
        suggested_fix="Run save_auth_profile first",
        retry_safe=False,
        stage="AUTH",
    )
    assert err.likely_cause == "Profile was never created"
    assert err.suggested_fix == "Run save_auth_profile first"
    assert err.retry_safe is False
    assert err.stage == "AUTH"


def test_tool_error_diagnostic_fields_default_empty():
    err = ToolError(code="BLOP_VALIDATION_FAILED", message="bad")
    assert err.likely_cause == ""
    assert err.suggested_fix == ""
    assert err.retry_safe is False
    assert err.stage is None


def test_err_response_forwards_diagnostic_fields():
    resp = err_response(
        "BLOP_AUTH_PROFILE_NOT_FOUND",
        "Profile not found",
        likely_cause="not created",
        suggested_fix="create it",
        retry_safe=False,
        stage="AUTH",
    )
    assert resp.ok is False
    assert resp.error.likely_cause == "not created"
    assert resp.error.suggested_fix == "create it"
    assert resp.error.stage == "AUTH"


def test_finalize_propagates_diagnostic_fields_into_mcp_error():
    raw = {
        "ok": False,
        "error": {
            "code": "BLOP_AUTH_PROFILE_NOT_FOUND",
            "message": "Profile not found",
            "likely_cause": "never created",
            "suggested_fix": "create it",
            "retry_safe": False,
            "stage": "AUTH",
        },
    }
    result = finalize_tool_payload(raw, request_id="req_1", tool_name="run_regression_test")
    mcp_err = result.get("mcp_error") or {}
    details = mcp_err.get("details") or {}
    assert details.get("likely_cause") == "never created"
    assert details.get("suggested_fix") == "create it"
