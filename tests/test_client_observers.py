"""Payload-safe outbound client outcome observers."""

import asyncio
from dataclasses import fields
from typing import cast

import httpx
import pytest
from _pytest.logging import LogCaptureFixture
from pydantic import BaseModel, model_validator

from tenchi.client import Client, ClientOutcome, UnexpectedResponseError
from tenchi.contracts import contract
from tenchi.errors import AppError, ConfigurationError, ErrorDef


class Item(BaseModel):
    name: str


missing = ErrorDef(code="ITEM_MISSING", status=404, message="Item missing")

get_item = contract(
    method="GET",
    path="/items",
    response=Item,
    errors=(missing,),
)
create_item = contract(
    method="POST",
    path="/items",
    request=Item,
    response=Item,
    status=201,
)


def test_client_observer_shape_is_checked_at_construction() -> None:
    def invalid() -> None:
        return None

    with pytest.raises(
        ConfigurationError,
        match=r"Client: observers\[0\].*one positional ClientOutcome",
    ):
        Client(
            base_url="https://api.example.test",
            observers=(invalid,),  # pyright: ignore[reportArgumentType]
        )


async def test_client_observers_receive_final_success_in_order(
    caplog: LogCaptureFixture,
) -> None:
    events: list[str] = []
    outcomes: list[ClientOutcome] = []

    async def respond(request: httpx.Request) -> httpx.Response:
        events.append("transport")
        return httpx.Response(200, json={"name": "milk"})

    def broken(outcome: ClientOutcome) -> None:
        events.append("broken")
        raise RuntimeError("observer failed")

    async def collect(outcome: ClientOutcome) -> None:
        events.append("collect")
        outcomes.append(outcome)

    async with Client(
        transport=httpx.MockTransport(respond),
        observers=(broken, collect),
    ) as client:
        result = await client.call(get_item)
        events.append("returned")

    assert result == Item(name="milk")
    assert events == ["transport", "broken", "collect", "returned"]
    assert "observer failed" in caplog.text
    assert len(outcomes) == 1
    outcome = outcomes[0]
    assert outcome.contract is get_item
    assert outcome.status == "succeeded"
    assert outcome.status_code == 200
    assert outcome.error_code is None
    assert outcome.duration_seconds >= 0
    assert {field.name for field in fields(outcome)} == {
        "contract",
        "status",
        "status_code",
        "duration_seconds",
        "error_code",
        "attempts",
        "completed_at",
    }


async def test_client_observers_cannot_mutate_outcomes_seen_later(
    caplog: LogCaptureFixture,
) -> None:
    outcomes: list[ClientOutcome] = []

    async def respond(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"name": "milk"})

    def mutate(outcome: ClientOutcome) -> None:
        cast(ClientOutcome, outcome).status = "failed"  # type: ignore[misc]

    def collect(outcome: ClientOutcome) -> None:
        outcomes.append(outcome)

    async with Client(
        transport=httpx.MockTransport(respond),
        observers=(mutate, collect),
    ) as client:
        await client.call(get_item)

    assert "cannot assign to field" in caplog.text
    assert outcomes[0].status == "succeeded"


async def test_client_observer_reports_declared_application_errors() -> None:
    outcomes: list[ClientOutcome] = []

    def broken(outcome: ClientOutcome) -> None:
        raise RuntimeError("observer must not replace AppError")

    async def respond(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            404,
            json={"code": missing.code, "message": missing.message},
            headers={
                "content-type": "application/json",
                "x-tenchi-error-source": "app",
            },
        )

    async with Client(
        transport=httpx.MockTransport(respond),
        observers=(broken, outcomes.append),
    ) as client:
        with pytest.raises(AppError) as excinfo:
            await client.call(get_item)

    assert excinfo.value.definition == missing
    assert len(outcomes) == 1
    assert outcomes[0].status == "app_error"
    assert outcomes[0].status_code == 404
    assert outcomes[0].error_code == "ITEM_MISSING"


async def test_client_observer_reports_invalid_responses_without_payloads() -> None:
    outcomes: list[ClientOutcome] = []

    async def respond(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"unexpected": "body"})

    async with Client(
        transport=httpx.MockTransport(respond),
        observers=(outcomes.append,),
    ) as client:
        with pytest.raises(UnexpectedResponseError):
            await client.call(get_item)

    assert len(outcomes) == 1
    assert outcomes[0].status == "unexpected_response"
    assert outcomes[0].status_code == 200
    assert outcomes[0].error_code is None


async def test_response_validator_app_error_is_normalized_without_remote_payload() -> (
    None
):
    outcomes: list[ClientOutcome] = []
    local_validation_error = ErrorDef(
        code="LOCAL_VALIDATION",
        status=409,
        message="Local validation failed",
    )

    class ValidatedItem(BaseModel):
        name: str

        @model_validator(mode="after")
        def reject(self) -> "ValidatedItem":
            raise AppError(local_validation_error)

    declared = contract(
        method="GET",
        path="/validated",
        response=ValidatedItem,
    )

    async def respond(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"name": "milk"})

    async with Client(
        transport=httpx.MockTransport(respond),
        observers=(outcomes.append,),
    ) as client:
        with pytest.raises(UnexpectedResponseError) as excinfo:
            await client.call(declared)

    assert excinfo.value.reason == "response body does not match the declared schema"
    assert excinfo.value.__cause__ is None
    assert excinfo.value.__context__ is None
    assert len(outcomes) == 1
    assert outcomes[0].status == "unexpected_response"
    assert outcomes[0].status_code == 200
    assert outcomes[0].error_code is None


async def test_client_observer_reports_transport_failures() -> None:
    outcomes: list[ClientOutcome] = []

    async def respond(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused", request=request)

    async with Client(
        transport=httpx.MockTransport(respond),
        observers=(outcomes.append,),
    ) as client:
        with pytest.raises(httpx.ConnectError):
            await client.call(get_item)

    assert len(outcomes) == 1
    assert outcomes[0].status == "transport_error"
    assert outcomes[0].status_code is None
    assert outcomes[0].error_code is None


async def test_client_observer_reports_local_failures_before_io() -> None:
    outcomes: list[ClientOutcome] = []
    requests: list[httpx.Request] = []

    async def respond(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(201, json={"name": "milk"})

    async with Client(
        transport=httpx.MockTransport(respond),
        observers=(outcomes.append,),
    ) as client:
        with pytest.raises(TypeError, match="pass request="):
            await client.call(create_item)

    assert requests == []
    assert len(outcomes) == 1
    assert outcomes[0].status == "failed"
    assert outcomes[0].status_code is None
    assert outcomes[0].error_code is None


async def test_client_observer_reports_cancellation() -> None:
    outcomes: list[ClientOutcome] = []
    started = asyncio.Event()

    async def respond(request: httpx.Request) -> httpx.Response:
        started.set()
        await asyncio.Event().wait()
        raise AssertionError("unreachable")

    async with Client(
        transport=httpx.MockTransport(respond),
        observers=(outcomes.append,),
    ) as client:
        pending = asyncio.create_task(client.call(get_item))
        await started.wait()
        pending.cancel()
        with pytest.raises(asyncio.CancelledError):
            await pending

    assert len(outcomes) == 1
    assert outcomes[0].status == "cancelled"
    assert outcomes[0].status_code is None
    assert outcomes[0].error_code is None
