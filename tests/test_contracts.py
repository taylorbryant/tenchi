import pytest
from pydantic import BaseModel

from tenchi.contracts import contract
from tenchi.errors import ErrorDef


class Item(BaseModel):
    name: str


def test_contract_defaults() -> None:
    declared = contract(method="get", path="/items", response=list[Item])

    assert declared.method == "GET"
    assert declared.path == "/items"
    assert declared.request is None
    assert declared.params is None
    assert declared.status == 200
    assert declared.errors == ()
    assert declared.name == "GET /items"


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
    assert declared.deprecated is False


def test_contract_rejects_empty_media_type() -> None:
    with pytest.raises(ValueError, match="media types must be non-empty"):
        contract(method="GET", path="/items", response_media_type="")
