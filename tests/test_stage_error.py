from blop.engine.errors import BlopError, StageError


def test_stage_error_fields():
    e = StageError(
        stage="AUTH",
        code="BLOP_AUTH_PROFILE_NOT_FOUND",
        message="Profile 'staging' not found.",
        likely_cause="Profile was never created.",
        suggested_fix="Run save_auth_profile first.",
        retry_safe=False,
    )
    assert e.stage == "AUTH"
    assert e.likely_cause == "Profile was never created."
    assert e.suggested_fix == "Run save_auth_profile first."
    assert e.retry_safe is False
    assert e.code == "BLOP_AUTH_PROFILE_NOT_FOUND"
    assert isinstance(e, BlopError)


def test_blop_error_diagnostic_fields_default_empty():
    e = BlopError("BLOP_VALIDATION_FAILED", "bad input")
    assert e.likely_cause == ""
    assert e.suggested_fix == ""
    assert e.retry_safe is False


def test_to_dict_includes_diagnostic_fields():
    """Verify that to_dict serializes diagnostic fields when set."""
    e = BlopError(
        "BLOP_VALIDATION_FAILED",
        "bad input",
        likely_cause="Missing required field",
        suggested_fix="Provide the field",
        retry_safe=True,
    )
    d = e.to_dict()
    assert d["error"]["likely_cause"] == "Missing required field"
    assert d["error"]["suggested_fix"] == "Provide the field"
    assert d["error"]["retry_safe"] is True


def test_to_dict_omits_empty_diagnostic_fields():
    """Verify that to_dict omits diagnostic fields when empty/False."""
    e = BlopError("BLOP_VALIDATION_FAILED", "bad input")
    d = e.to_dict()
    assert "likely_cause" not in d["error"]
    assert "suggested_fix" not in d["error"]
    assert "retry_safe" not in d["error"]


def test_stage_error_injects_stage_into_details():
    """Verify that StageError injects stage into details dict."""
    e = StageError(
        stage="AUTH",
        code="BLOP_AUTH_PROFILE_NOT_FOUND",
        message="Profile 'staging' not found.",
        likely_cause="Profile was never created.",
        suggested_fix="Run save_auth_profile first.",
        retry_safe=False,
    )
    d = e.to_dict()
    assert d["error"]["details"]["stage"] == "AUTH"


def test_stage_error_to_dict_includes_diagnostic_fields():
    """Verify that StageError.to_dict() includes diagnostic fields."""
    e = StageError(
        stage="AUTH",
        code="BLOP_AUTH_PROFILE_NOT_FOUND",
        message="Profile 'staging' not found.",
        likely_cause="Profile was never created.",
        suggested_fix="Run save_auth_profile first.",
        retry_safe=False,
    )
    d = e.to_dict()
    assert d["error"]["likely_cause"] == "Profile was never created."
    assert d["error"]["suggested_fix"] == "Run save_auth_profile first."
