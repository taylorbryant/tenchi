import pytest
from pydantic import BaseModel

from tenchi.contracts import contract
from tenchi.errors import ConfigurationError, ErrorDef


class Item(BaseModel):
    name: str


def test_contract_defaults() -> None:
    declared = contract(method="get", path="/items", response=list[Item])

    assert declared.method == "GET"
    assert declared.path == "/items"
    assert declared.request is None
    assert declared.params is None
    assert declared.response_headers is None
    assert declared.status == 200
    assert declared.errors == ()
    assert declared.name == "GET /items"
    assert declared.successes == ()
    assert declared.timeout is None
    assert declared.public is False


@pytest.mark.parametrize("timeout", [0, -1, float("inf"), float("nan")])
def test_contract_rejects_invalid_timeout(timeout: float) -> None:
    with pytest.raises(ConfigurationError, match="timeout must be finite and positive"):
        contract(method="GET", path="/items", timeout=timeout)


def test_contract_rejects_malformed_timeout_type() -> None:
    with pytest.raises(ConfigurationError, match="timeout must be a number"):
        contract(method="GET", path="/items", timeout=True)  # type: ignore[arg-type]


def test_contract_carries_declared_errors() -> None:
    missing = ErrorDef(code="ITEM_MISSING", status=404, message="Item missing")
    other = ErrorDef(code="OTHER", status=409, message="Other")

    declared = contract(
        method="GET", path="/items/{item_id}", response=Item, errors=(missing,)
    )

    assert declared.declares_error(missing)
    assert not declared.declares_error(other)


def test_contract_rejects_unknown_method() -> None:
    with pytest.raises(ValueError, match="unsupported HTTP method"):
        contract(method="FETCH", path="/items")


def test_contract_rejects_relative_path() -> None:
    with pytest.raises(ValueError, match="must start with '/'"):
        contract(method="GET", path="items")


@pytest.mark.parametrize("path", ["/items/{item-id}", "/items/{item_id", "/items/}"])
def test_contract_rejects_malformed_path_parameter_syntax(path: str) -> None:
    with pytest.raises(ConfigurationError, match="invalid path parameter syntax"):
        contract(method="GET", path=path)


def test_contract_rejects_invalid_status() -> None:
    with pytest.raises(ValueError, match="invalid status"):
        contract(method="GET", path="/items", status=42)


def test_contract_metadata_defaults() -> None:
    declared = contract(method="GET", path="/items", response=list[Item])

    assert declared.request_media_type == "application/json"
    assert declared.response_media_type == "application/json"
    assert declared.summary is None
    assert declared.description is None
    assert declared.tags == ()
    assert declared.public is False
    assert declared.deprecated is False


def test_contract_carries_explicit_public_metadata() -> None:
    declared = contract(method="GET", path="/health", public=True)

    assert declared.public is True


def test_contract_rejects_malformed_public_metadata() -> None:
    with pytest.raises(ConfigurationError, match="public must be a bool"):
        contract(method="GET", path="/items", public=1)  # type: ignore[arg-type]


def test_contract_rejects_empty_media_type() -> None:
    with pytest.raises(ValueError, match="media types must be non-empty"):
        contract(method="GET", path="/items", response_media_type="")
    with pytest.raises(ValueError, match="media types must be non-empty"):
        contract(method="GET", path="/items", response_media_type="  ")


def test_contract_rejects_unsupported_declared_charsets() -> None:
    with pytest.raises(
        ConfigurationError, match=r"request_media_type.*unsupported charset"
    ):
        contract(
            method="POST",
            path="/items",
            request=str,
            request_media_type="text/plain; charset=not-a-codec",
        )
    with pytest.raises(
        ConfigurationError, match=r"response_media_type.*unsupported charset"
    ):
        contract(
            method="GET",
            path="/items",
            response=str,
            response_media_type="text/plain; charset=not-a-codec",
        )


def test_contract_rejects_extended_media_type_parameters() -> None:
    with pytest.raises(
        ConfigurationError, match="extended media type parameters are unsupported"
    ):
        contract(
            method="GET",
            path="/items",
            response=str,
            response_media_type="text/plain; charset*=utf-8''utf-8",
        )


def test_contract_rejects_malformed_text_metadata() -> None:
    with pytest.raises(ConfigurationError, match="name must be a string"):
        contract(method="GET", path="/items", name=42)  # type: ignore[arg-type]
    with pytest.raises(ConfigurationError, match="summary must be a string"):
        contract(method="GET", path="/items", summary=42)  # type: ignore[arg-type]
    with pytest.raises(ConfigurationError, match="description must be a string"):
        contract(method="GET", path="/items", description=42)  # type: ignore[arg-type]


def test_contract_rejects_malformed_declaration_collections() -> None:
    with pytest.raises(ConfigurationError, match="tags must be a sequence"):
        contract(method="GET", path="/items", tags="items")

    with pytest.raises(ConfigurationError, match=r"errors\[0\].*ErrorDef"):
        contract(
            method="GET",
            path="/items",
            errors=("ITEM_MISSING",),  # type: ignore[arg-type]
        )


def test_contract_rejects_conflicting_error_codes_and_dedupes_identical_defs() -> None:
    first = ErrorDef(code="CONFLICT", status=409, message="First meaning")
    conflicting = ErrorDef(code="CONFLICT", status=409, message="Second meaning")

    with pytest.raises(ConfigurationError, match=r"conflicting ErrorDef.*CONFLICT"):
        contract(method="GET", path="/items", errors=(first, conflicting))

    declared = contract(method="GET", path="/items", errors=(first, first))

    assert declared.errors == (first,)
