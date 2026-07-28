"""Explicit outbound retries and attempt-level observability."""

import asyncio

import httpx
import pytest
from pydantic import BaseModel, ValidationError

from tenchi.client import Client, ClientAttemptOutcome, ClientOutcome
from tenchi.contracts import contract
from tenchi.errors import ConfigurationError, ErrorDef
from tenchi.retries import RetryTimeoutError, retry_policy


class Item(BaseModel):
    name: str


temporarily_unavailable = ErrorDef(
    code="TEMPORARILY_UNAVAILABLE",
    status=503,
    message="Temporarily unavailable",
    headers=("Retry-After",),
)

get_item = contract(
    method="GET",
    path="/items/1",
    response=Item,
    errors=(temporarily_unavailable,),
)
create_item = contract(
    method="POST",
    path="/items",
    request=Item,
    response=Item,
)


async def test_retries_declared_errors_and_respects_retry_after(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    requests = 0
    sleeps: list[float] = []
    attempts: list[ClientAttemptOutcome] = []
    outcomes: list[ClientOutcome] = []

    async def respond(request: httpx.Request) -> httpx.Response:
        nonlocal requests
        requests += 1
        if requests < 3:
            return httpx.Response(
                503,
                json={
                    "code": temporarily_unavailable.code,
                    "message": temporarily_unavailable.message,
                },
                headers={
                    "content-type": "application/json",
                    "x-tenchi-error-source": "app",
                    "retry-after": "2",
                },
            )
        return httpx.Response(200, json={"name": "milk"})

    async def fake_sleep(delay: float) -> None:
        sleeps.append(delay)

    monkeypatch.setattr("tenchi.client.asyncio.sleep", fake_sleep)
    async with Client(
        transport=httpx.MockTransport(respond),
        observers=(outcomes.append,),
        attempt_observers=(attempts.append,),
    ) as client:
        result = await client.call(
            get_item,
            retry=retry_policy(
                retry_on=(temporarily_unavailable.code,),
                base_delay_seconds=0,
                max_delay_seconds=0,
                jitter=0,
            ),
        )

    assert result == Item(name="milk")
    assert sleeps == [2.0, 2.0]
    assert [item.status for item in attempts] == [
        "app_error",
        "app_error",
        "succeeded",
    ]
    assert [item.will_retry for item in attempts] == [True, True, False]
    assert [item.retry_delay_seconds for item in attempts] == [2.0, 2.0, None]
    assert len(outcomes) == 1
    assert outcomes[0].status == "succeeded"
    assert outcomes[0].attempts == 3
    assert outcomes[0].status_code == 200


async def test_retries_transport_errors_but_not_invalid_responses() -> None:
    requests = 0

    async def recover(request: httpx.Request) -> httpx.Response:
        nonlocal requests
        requests += 1
        if requests == 1:
            raise httpx.ConnectError("offline", request=request)
        return httpx.Response(200, json={"name": "milk"})

    policy = retry_policy(
        base_delay_seconds=0,
        max_delay_seconds=0,
        jitter=0,
    )
    async with Client(transport=httpx.MockTransport(recover)) as client:
        assert await client.call(get_item, retry=policy) == Item(name="milk")
    assert requests == 2

    requests = 0

    async def invalid(request: httpx.Request) -> httpx.Response:
        nonlocal requests
        requests += 1
        return httpx.Response(200, json={"wrong": "shape"})

    async with Client(transport=httpx.MockTransport(invalid)) as client:
        with pytest.raises(ValidationError):
            await client.call(get_item, retry=policy)
    assert requests == 1


async def test_retry_configuration_fails_before_io() -> None:
    requests = 0

    async def respond(request: httpx.Request) -> httpx.Response:
        nonlocal requests
        requests += 1
        return httpx.Response(200, json={"name": "milk"})

    async with Client(transport=httpx.MockTransport(respond)) as client:
        with pytest.raises(
            ConfigurationError,
            match="retrying POST requires allow_unsafe_methods=True",
        ):
            await client.call(
                create_item,
                request=Item(name="milk"),
                retry=retry_policy(),
            )
        with pytest.raises(ConfigurationError, match="undeclared error code"):
            await client.call(
                get_item,
                retry=retry_policy(retry_on=("UNKNOWN",)),
            )
    assert requests == 0


async def test_retry_deadline_cancels_the_attempt_and_reports_timeout() -> None:
    attempts: list[ClientAttemptOutcome] = []
    outcomes: list[ClientOutcome] = []
    started = asyncio.Event()

    async def wait_forever(request: httpx.Request) -> httpx.Response:
        started.set()
        await asyncio.Event().wait()
        raise AssertionError("unreachable")

    async with Client(
        transport=httpx.MockTransport(wait_forever),
        observers=(outcomes.append,),
        attempt_observers=(attempts.append,),
    ) as client:
        with pytest.raises(RetryTimeoutError) as excinfo:
            await client.call(
                get_item,
                retry=retry_policy(total_timeout_seconds=0.01),
            )

    assert started.is_set()
    assert excinfo.value.attempts == 1
    assert [item.status for item in attempts] == ["timed_out"]
    assert [item.status for item in outcomes] == ["timed_out"]
    assert outcomes[0].attempts == 1


async def test_retry_deadline_can_expire_during_backoff() -> None:
    requests = 0
    attempts: list[ClientAttemptOutcome] = []
    outcomes: list[ClientOutcome] = []

    async def offline(request: httpx.Request) -> httpx.Response:
        nonlocal requests
        requests += 1
        raise httpx.ConnectError("offline", request=request)

    async with Client(
        transport=httpx.MockTransport(offline),
        observers=(outcomes.append,),
        attempt_observers=(attempts.append,),
    ) as client:
        with pytest.raises(RetryTimeoutError) as excinfo:
            await client.call(
                get_item,
                retry=retry_policy(
                    base_delay_seconds=1,
                    max_delay_seconds=1,
                    jitter=0,
                    total_timeout_seconds=0.01,
                ),
            )

    assert requests == 1
    assert excinfo.value.attempts == 1
    assert [item.status for item in attempts] == ["transport_error"]
    assert attempts[0].will_retry is True
    assert [item.status for item in outcomes] == ["timed_out"]


async def test_external_cancellation_is_not_converted_to_retry_timeout() -> None:
    attempts: list[ClientAttemptOutcome] = []
    outcomes: list[ClientOutcome] = []
    started = asyncio.Event()

    async def wait_forever(request: httpx.Request) -> httpx.Response:
        started.set()
        await asyncio.Event().wait()
        raise AssertionError("unreachable")

    async with Client(
        transport=httpx.MockTransport(wait_forever),
        observers=(outcomes.append,),
        attempt_observers=(attempts.append,),
    ) as client:
        running = asyncio.create_task(
            client.call(
                get_item,
                retry=retry_policy(total_timeout_seconds=10),
            )
        )
        await started.wait()
        running.cancel()
        with pytest.raises(asyncio.CancelledError):
            await running

    assert [item.status for item in attempts] == ["cancelled"]
    assert [item.status for item in outcomes] == ["cancelled"]


async def test_attempt_observer_time_does_not_consume_the_retry_deadline() -> None:
    attempts: list[ClientAttemptOutcome] = []

    async def respond(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"name": "milk"})

    async def slow_observer(outcome: ClientAttemptOutcome) -> None:
        await asyncio.sleep(0.02)
        attempts.append(outcome)

    async with Client(
        transport=httpx.MockTransport(respond),
        attempt_observers=(slow_observer,),
    ) as client:
        result = await client.call(
            get_item,
            retry=retry_policy(total_timeout_seconds=0.01),
        )

    assert result == Item(name="milk")
    assert [item.status for item in attempts] == ["succeeded"]


async def test_attempt_observer_time_does_not_consume_deadline_between_retries() -> (
    None
):
    requests = 0

    async def respond(request: httpx.Request) -> httpx.Response:
        nonlocal requests
        requests += 1
        if requests == 1:
            raise httpx.ConnectError("offline", request=request)
        return httpx.Response(200, json={"name": "milk"})

    async def slow_observer(outcome: ClientAttemptOutcome) -> None:
        await asyncio.sleep(0.02)

    async with Client(
        transport=httpx.MockTransport(respond),
        attempt_observers=(slow_observer,),
    ) as client:
        result = await client.call(
            get_item,
            retry=retry_policy(
                base_delay_seconds=0,
                max_delay_seconds=0,
                total_timeout_seconds=0.01,
            ),
        )

    assert result == Item(name="milk")
    assert requests == 2


async def test_user_timeout_error_is_not_misreported_as_retry_deadline() -> None:
    outcomes: list[ClientOutcome] = []
    attempts: list[ClientAttemptOutcome] = []

    async def respond(request: httpx.Request) -> httpx.Response:
        raise TimeoutError("application timeout")

    async with Client(
        transport=httpx.MockTransport(respond),
        observers=(outcomes.append,),
        attempt_observers=(attempts.append,),
    ) as client:
        with pytest.raises(TimeoutError, match="application timeout"):
            await client.call(
                get_item,
                retry=retry_policy(total_timeout_seconds=1),
            )

    assert [item.status for item in attempts] == ["failed"]
    assert [item.status for item in outcomes] == ["failed"]


def test_retry_policy_accepts_sequences_and_rejects_invalid_values() -> None:
    policy = retry_policy(retry_on=[temporarily_unavailable.code])
    assert policy.retry_on == (temporarily_unavailable.code,)

    with pytest.raises(ConfigurationError, match="sequence of error codes"):
        retry_policy(retry_on="TEMPORARILY_UNAVAILABLE")  # pyright: ignore[reportArgumentType]


async def test_jitter_never_exceeds_the_declared_max_delay(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    requests = 0
    sleeps: list[float] = []

    async def respond(request: httpx.Request) -> httpx.Response:
        nonlocal requests
        requests += 1
        if requests == 1:
            raise httpx.ConnectError("offline", request=request)
        return httpx.Response(200, json={"name": "milk"})

    async def fake_sleep(delay: float) -> None:
        sleeps.append(delay)

    def upper_bound(start: float, end: float) -> float:
        del start
        return end

    monkeypatch.setattr("tenchi.client.random.uniform", upper_bound)
    monkeypatch.setattr("tenchi.client.asyncio.sleep", fake_sleep)
    async with Client(transport=httpx.MockTransport(respond)) as client:
        await client.call(
            get_item,
            retry=retry_policy(
                base_delay_seconds=1,
                max_delay_seconds=1,
                jitter=1,
            ),
        )

    assert sleeps == [1]
