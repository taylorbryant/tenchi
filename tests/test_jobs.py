"""Validated background-job declarations, messages, and dispatch."""

import asyncio
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from dataclasses import FrozenInstanceError, dataclass
from typing import cast

import pytest
from pydantic import BaseModel, ValidationError

from tenchi.errors import ConfigurationError
from tenchi.execution import UseCaseOutcome
from tenchi.jobs import (
    JobBindingError,
    JobNotFoundError,
    JobResultError,
    create_job_dispatcher,
    job,
    job_group,
    job_handler,
    job_message,
)


class Delivery(BaseModel):
    message_id: str
    address: str


class DeliveryResult(BaseModel):
    accepted: bool


@dataclass(frozen=True, slots=True)
class Context:
    events: list[str]


delivery_job = job(
    "mail.deliver",
    request=Delivery,
    result=DeliveryResult,
    description="Deliver one queued message.",
)


async def deliver(request: Delivery, context: Context) -> DeliveryResult:
    context.events.append(f"delivered:{request.message_id}")
    return DeliveryResult(accepted=True)


def test_job_message_validates_and_serializes_without_repr_payload() -> None:
    message = job_message(
        delivery_job,
        Delivery(message_id="m1", address="user@example.com"),
    )

    assert message.name == "mail.deliver"
    assert message.payload_json == (b'{"message_id":"m1","address":"user@example.com"}')
    assert "user@example.com" not in repr(message)
    with pytest.raises(FrozenInstanceError):
        cast(object, message).name = "changed"  # type: ignore[attr-defined]
    with pytest.raises(ValidationError):
        job_message(
            delivery_job,
            {"message_id": "m1"},  # pyright: ignore[reportArgumentType]
        )


def test_job_message_rejects_json_the_consumer_cannot_read() -> None:
    floating_point_job = job("math.record", request=float, result=None)

    with pytest.raises(ValidationError):
        job_message(floating_point_job, float("inf"))


def test_job_handler_validates_exact_annotations_and_shape() -> None:
    async def wrong_request(request: str, context: Context) -> DeliveryResult:
        return DeliveryResult(accepted=True)

    async def wrong_result(request: Delivery, context: Context) -> str:
        return "yes"

    def sync_handler(request: Delivery, context: Context) -> DeliveryResult:
        return DeliveryResult(accepted=True)

    with pytest.raises(JobBindingError, match="request annotation"):
        job_handler(delivery_job, wrong_request)  # pyright: ignore[reportArgumentType]
    with pytest.raises(JobBindingError, match="return annotation"):
        job_handler(delivery_job, wrong_result)  # pyright: ignore[reportArgumentType]
    with pytest.raises(JobBindingError, match="must be an async function"):
        job_handler(delivery_job, sync_handler)  # pyright: ignore[reportArgumentType]


async def test_dispatch_validates_input_and_result_inside_context_scope() -> None:
    events: list[str] = []
    observed: list[UseCaseOutcome] = []

    @asynccontextmanager
    async def context() -> AsyncGenerator[Context]:
        events.append("enter")
        try:
            yield Context(events)
        finally:
            events.append("exit")

    def observe(outcome: UseCaseOutcome) -> None:
        events.append("observe")
        observed.append(outcome)

    dispatcher = create_job_dispatcher(
        jobs=job_group(job_handler(delivery_job, deliver)),
        use_case_observers=(observe,),
    )
    result = await dispatcher.dispatch(
        "mail.deliver",
        payload_json=job_message(
            delivery_job,
            Delivery(message_id="m1", address="user@example.com"),
        ).payload_json,
        context=context,
    )

    assert result == DeliveryResult(accepted=True)
    assert events == ["enter", "delivered:m1", "exit", "observe"]
    assert len(observed) == 1
    assert observed[0].entrypoint == "job"
    assert observed[0].status == "succeeded"

    with pytest.raises(ValidationError):
        await dispatcher.dispatch(
            "mail.deliver",
            payload_json=b'{"message_id":"missing-address"}',
            context=context,
        )
    assert events == ["enter", "delivered:m1", "exit", "observe"]


async def test_invalid_result_reaches_context_exit_before_raising() -> None:
    events: list[str] = []

    async def invalid(request: Delivery, context: Context) -> DeliveryResult:
        context.events.append("handler")
        return cast(DeliveryResult, {"accepted": "not-a-bool"})

    @asynccontextmanager
    async def context() -> AsyncGenerator[Context]:
        try:
            yield Context(events)
        except JobResultError:
            events.append("rollback")
            raise

    dispatcher = create_job_dispatcher(
        jobs=job_group(job_handler(delivery_job, invalid))
    )
    with pytest.raises(JobResultError):
        await dispatcher.dispatch(
            "mail.deliver",
            payload_json=b'{"message_id":"m1","address":"user@example.com"}',
            context=context,
        )
    assert events == ["handler", "rollback"]


async def test_unknown_job_never_opens_context() -> None:
    opened = False

    @asynccontextmanager
    async def context() -> AsyncGenerator[Context]:
        nonlocal opened
        opened = True
        yield Context([])

    dispatcher = create_job_dispatcher(jobs=job_group())
    with pytest.raises(JobNotFoundError, match="unknown job"):
        await dispatcher.dispatch(
            "mail.unknown",
            payload_json=b"{}",
            context=context,
        )
    assert opened is False


async def test_dispatch_cancellation_closes_context_and_notifies() -> None:
    entered = asyncio.Event()
    exited = asyncio.Event()
    observed: list[UseCaseOutcome] = []

    async def wait(request: Delivery, context: Context) -> DeliveryResult:
        entered.set()
        await asyncio.Event().wait()
        raise AssertionError("unreachable")

    @asynccontextmanager
    async def context() -> AsyncGenerator[Context]:
        try:
            yield Context([])
        finally:
            exited.set()

    dispatcher = create_job_dispatcher(
        jobs=job_group(job_handler(delivery_job, wait)),
        use_case_observers=(observed.append,),
    )
    running = asyncio.create_task(
        dispatcher.dispatch(
            "mail.deliver",
            payload_json=b'{"message_id":"m1","address":"user@example.com"}',
            context=context,
        )
    )
    await entered.wait()
    running.cancel()
    with pytest.raises(asyncio.CancelledError):
        await running

    assert exited.is_set()
    assert len(observed) == 1
    assert observed[0].entrypoint == "job"
    assert observed[0].status == "cancelled"


def test_job_groups_reject_duplicate_names_and_invalid_composition() -> None:
    handler = job_handler(delivery_job, deliver)
    with pytest.raises(ConfigurationError, match="duplicate job name"):
        job_group(handler, handler)
    with pytest.raises(ConfigurationError, match="must be a JobHandler"):
        job_group(cast(object, delivery_job))  # pyright: ignore[reportArgumentType]
