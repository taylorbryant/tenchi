import pytest

from tenchi.errors import AppError, ConfigurationError, ErrorDef, error_body

item_missing = ErrorDef(code="ITEM_MISSING", status=404, message="Item missing")


def test_app_error_defaults_to_definition_message() -> None:
    error = AppError(item_missing)

    assert error.code == "ITEM_MISSING"
    assert error.status == 404
    assert error.message == "Item missing"
    assert error.details is None


def test_app_error_accepts_override_message_and_details() -> None:
    error = AppError(
        item_missing,
        message="Item abc123 is missing",
        details={"item_id": "abc123"},
    )

    assert error.message == "Item abc123 is missing"
    assert error.details == {"item_id": "abc123"}


def test_app_error_carries_response_headers() -> None:
    throttled = ErrorDef(
        code="THROTTLED",
        status=429,
        message="Slow down",
        headers=("Retry-After",),
    )

    error = AppError(throttled, headers={"Retry-After": "30"})

    assert throttled.headers == ("Retry-After",)
    assert error.headers == {"Retry-After": "30"}
    assert AppError(throttled).headers == {}


def test_error_def_rejects_invalid_declarations() -> None:
    with pytest.raises(ConfigurationError, match="SCREAMING_SNAKE_CASE"):
        ErrorDef(code="item-missing", status=404, message="Missing")
    with pytest.raises(ConfigurationError, match="status must be between 400 and 599"):
        ErrorDef(code="NO_ERROR", status=200, message="Not an error")
    with pytest.raises(ConfigurationError, match="message must be non-empty"):
        ErrorDef(code="EMPTY", status=400, message="")
    with pytest.raises(ConfigurationError, match="header names must be non-empty"):
        ErrorDef(code="BAD_HEADER", status=400, message="Bad", headers=("",))
    with pytest.raises(ConfigurationError, match="not a valid HTTP header"):
        ErrorDef(code="BAD_HEADER", status=400, message="Bad", headers=("bad name",))
    with pytest.raises(ConfigurationError, match="reserved by the Tenchi framework"):
        ErrorDef(
            code="BAD_HEADER",
            status=400,
            message="Bad",
            headers=("x-request-id",),
        )
    with pytest.raises(ConfigurationError, match="reserved by the Tenchi framework"):
        ErrorDef(
            code="BAD_HEADER",
            status=400,
            message="Bad",
            headers=("Content-Type",),
        )
    with pytest.raises(ConfigurationError, match="reserved by the Tenchi framework"):
        ErrorDef(
            code="BAD_HEADER",
            status=400,
            message="Bad",
            headers=("Transfer-Encoding",),
        )


def test_app_error_rejects_undeclared_headers() -> None:
    with pytest.raises(ConfigurationError, match=r"header.*not declared"):
        AppError(item_missing, headers={"Retry-After": "30"})


def test_app_error_rejects_malformed_definition_and_message() -> None:
    with pytest.raises(ConfigurationError, match="definition must be an ErrorDef"):
        AppError("ITEM_MISSING")  # type: ignore[arg-type]
    with pytest.raises(ConfigurationError, match="message must be a string or None"):
        AppError(item_missing, message=42)  # type: ignore[arg-type]


def test_app_error_header_declarations_are_case_insensitive() -> None:
    throttled = ErrorDef(
        code="THROTTLED",
        status=429,
        message="Slow down",
        headers=("Retry-After",),
    )

    error = AppError(throttled, headers={"retry-after": "30"})

    assert error.headers == {"retry-after": "30"}


def test_app_error_rejects_unsafe_or_duplicate_header_values() -> None:
    throttled = ErrorDef(
        code="THROTTLED",
        status=429,
        message="Slow down",
        headers=("Retry-After",),
    )

    with pytest.raises(ConfigurationError, match="must not contain CR or LF"):
        AppError(throttled, headers={"Retry-After": "30\r\nx-injected: yes"})
    with pytest.raises(ConfigurationError, match="start or end with whitespace"):
        AppError(throttled, headers={"Retry-After": " 30"})
    with pytest.raises(ConfigurationError, match="control characters"):
        AppError(throttled, headers={"Retry-After": "30\x00"})
    with pytest.raises(ConfigurationError, match="must be Latin-1 encodable"):
        AppError(throttled, headers={"Retry-After": "later ☃"})
    with pytest.raises(ConfigurationError, match="provided more than once"):
        AppError(
            throttled,
            headers={"Retry-After": "30", "retry-after": "60"},
        )


def test_error_body_omits_absent_details() -> None:
    assert error_body(code="X", message="y") == {"code": "X", "message": "y"}
    assert error_body(code="X", message="y", details=[1]) == {
        "code": "X",
        "message": "y",
        "details": [1],
    }
