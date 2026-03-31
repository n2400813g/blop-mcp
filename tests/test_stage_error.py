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
