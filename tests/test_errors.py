from tenchi.errors import AppError, ErrorDef, error_body

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


def test_error_body_omits_absent_details() -> None:
    assert error_body(code="X", message="y") == {"code": "X", "message": "y"}
    assert error_body(code="X", message="y", details=[1]) == {
        "code": "X",
        "message": "y",
        "details": [1],
    }
