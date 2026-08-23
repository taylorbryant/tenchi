from typing import Annotated

import pytest
from pydantic import BaseModel, Field, PlainValidator

from tenchi.contracts import Contract, contract
from tenchi.errors import ConfigurationError, ErrorDef


class Item(BaseModel):
    name: str


type BinaryAlias = bytes
type OptionalTextAlias = str | None
type GenericAlias[T] = T
type ValidatedText = Annotated[Item, PlainValidator(lambda _: "rendered")]


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
    assert declared.responses == ()
    assert declared.timeout is None
    assert declared.public is False
    assert declared.webhook is False
    assert declared.idempotency_key is False
    assert declared.request_examples == ()
    assert declared.response_examples == ()


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


@pytest.mark.parametrize("status", [42, 199, 400, 599])
def test_contract_rejects_non_success_status(status: int) -> None:
    with pytest.raises(ValueError, match="status must be between 200 and 399"):
        contract(method="GET", path="/items", status=status)


@pytest.mark.parametrize("status", [204, 205, 304])
def test_singular_contract_rejects_a_body_for_bodyless_status(status: int) -> None:
    with pytest.raises(
        ConfigurationError, match=rf"status {status} cannot declare a response body"
    ):
        contract(method="GET", path="/items", response=Item, status=status)

    with pytest.raises(
        ConfigurationError, match=rf"status {status} cannot declare a response body"
    ):
        Contract(method="GET", path="/items", response=Item, status=status)


def test_contract_metadata_defaults() -> None:
    declared = contract(method="GET", path="/items", response=list[Item])

    assert declared.request_media_type == "application/json"
    assert declared.response_media_type == "application/json"
    assert declared.summary is None
    assert declared.description is None
    assert declared.tags == ()
    assert declared.public is False
    assert declared.webhook is False
    assert declared.deprecated is False


def test_contract_carries_explicit_public_metadata() -> None:
    declared = contract(method="GET", path="/health", public=True)

    assert declared.public is True


def test_contract_rejects_malformed_public_metadata() -> None:
    with pytest.raises(ConfigurationError, match="public must be a bool"):
        contract(method="GET", path="/items", public=1)  # type: ignore[arg-type]


def test_contract_carries_and_validates_webhook_metadata() -> None:
    declared = contract(
        method="POST",
        path="/webhook",
        request=Item,
        webhook=True,
    )

    assert declared.webhook is True
    with pytest.raises(ConfigurationError, match="webhook must be a bool"):
        contract(
            method="POST",
            path="/webhook",
            request=Item,
            webhook=1,  # type: ignore[arg-type]
        )
    with pytest.raises(ConfigurationError, match="requires a request type"):
        contract(method="POST", path="/webhook", webhook=True)


def test_contract_carries_idempotency_and_named_examples() -> None:
    class CommandHeaders(BaseModel):
        idempotency_key: str = Field(min_length=1)

    request_example = Item(name="create")
    response_example = Item(name="created")

    declared = contract(
        method="POST",
        path="/items",
        request=Item,
        headers=CommandHeaders,
        response=Item,
        idempotency_key=True,
        request_examples={"create": request_example},
        response_examples={"created": response_example},
    )

    assert declared.idempotency_key is True
    assert declared.request_examples == (("create", request_example),)
    assert declared.response_examples == (("created", response_example),)


def test_contract_rejects_idempotency_without_an_unsafe_header_boundary() -> None:
    class CommandHeaders(BaseModel):
        idempotency_key: str

    with pytest.raises(ConfigurationError, match="idempotency_key must be a bool"):
        contract(
            method="POST",
            path="/items",
            headers=CommandHeaders,
            idempotency_key=1,  # type: ignore[arg-type]
        )
    with pytest.raises(ConfigurationError, match="only valid for unsafe methods"):
        contract(
            method="GET",
            path="/items",
            headers=CommandHeaders,
            idempotency_key=True,
        )
    with pytest.raises(ConfigurationError, match="requires a headers type"):
        contract(method="POST", path="/items", idempotency_key=True)


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("request_examples", {"create": Item(name="create")}, "no request type"),
        ("response_examples", {"ok": Item(name="ok")}, "no response body"),
        ("request_examples", {"": Item(name="create")}, "non-empty string"),
    ],
)
def test_contract_rejects_examples_without_a_named_body(
    field: str, value: object, message: str
) -> None:
    kwargs: dict[str, object] = {
        "method": "POST",
        "path": "/items",
        field: value,
    }
    if field == "request_examples" and message == "non-empty string":
        kwargs["request"] = Item

    with pytest.raises(ConfigurationError, match=message):
        contract(**kwargs)  # type: ignore[arg-type]


def test_direct_contract_construction_cannot_bypass_new_invariants() -> None:
    with pytest.raises(ConfigurationError, match="requires a headers type"):
        Contract(method="POST", path="/items", idempotency_key=True)
    with pytest.raises(ConfigurationError, match="named example entries"):
        Contract(  # type: ignore[arg-type]
            method="POST",
            path="/items",
            request=Item,
            request_examples=("not-an-entry",),  # pyright: ignore[reportArgumentType]
        )
    with pytest.raises(ConfigurationError, match="repeats example name 'same'"):
        Contract(
            method="POST",
            path="/items",
            request=Item,
            request_examples=(
                ("same", Item(name="one")),
                ("same", Item(name="two")),
            ),
        )


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


@pytest.mark.parametrize("charset", ["zip", "base64", "hex", "rot_13", "quopri"])
def test_contract_rejects_non_text_declared_codecs(charset: str) -> None:
    with pytest.raises(
        ConfigurationError, match=r"response_media_type.*unsupported charset"
    ):
        contract(
            method="GET",
            path="/items",
            response=str,
            response_media_type=f"text/plain; charset={charset}",
        )


@pytest.mark.parametrize(
    ("field", "annotation", "media_type", "message"),
    [
        ("request", Item, "text/plain", "request body.*str-shaped"),
        ("response", Item, "text/plain", "response body.*str-shaped"),
        (
            "request",
            Item,
            "application/octet-stream",
            "request body.*str- or bytes-shaped",
        ),
        (
            "response",
            Item,
            "application/octet-stream",
            "response body.*str- or bytes-shaped",
        ),
        ("response", bytes, "application/json", "bytes cannot use a JSON"),
        ("response", BinaryAlias, "application/json", "bytes cannot use a JSON"),
        (
            "response",
            GenericAlias[bytes],
            "application/json",
            "bytes cannot use a JSON",
        ),
        (
            "response",
            str | None,
            "text/plain",
            "response body.*str-shaped",
        ),
        (
            "response",
            OptionalTextAlias,
            "text/plain",
            "response body.*str-shaped",
        ),
    ],
)
def test_contract_rejects_incompatible_body_media_pairings(
    field: str,
    annotation: object,
    media_type: str,
    message: str,
) -> None:
    kwargs: dict[str, object] = {
        "method": "POST",
        "path": "/items",
        field: annotation,
        f"{field}_media_type": media_type,
    }
    with pytest.raises(ConfigurationError, match=message):
        contract(**kwargs)  # type: ignore[arg-type]


def test_direct_contract_construction_enforces_body_media_pairings() -> None:
    with pytest.raises(ConfigurationError, match=r"response body.*str-shaped"):
        Contract(
            method="GET",
            path="/items",
            response=Item,
            response_media_type="text/plain",
        )


def test_nullable_body_pairings_follow_the_runtime_direction() -> None:
    request = contract(
        method="POST",
        path="/request",
        request=str | None,
        request_media_type="text/plain",
    )
    response = contract(
        method="GET",
        path="/response",
        response=str | None,
    )
    aliased_response = contract(
        method="GET",
        path="/aliased-response",
        response=GenericAlias[str],  # type: ignore[arg-type]
        response_media_type="text/plain",
    )

    assert request.request == str | None
    assert response.response == str | None
    assert aliased_response.response == GenericAlias[str]


def test_custom_validation_output_is_not_rejected_from_its_wrapped_type() -> None:
    declared = contract(
        method="GET",
        path="/validated-text",
        response=ValidatedText,  # type: ignore[arg-type]
        response_media_type="text/plain",
    )

    assert declared.response == ValidatedText


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
