"""Environment-aware deployment preflight checks."""

import asyncio

import pytest

from tenchi.errors import ConfigurationError
from tenchi.preflight import (
    PreflightBindingError,
    preflight_check,
    preflight_group,
    run_preflight,
)


async def ready() -> None:
    return None


def test_preflight_declaration_is_stable_and_redaction_owned() -> None:
    declared = preflight_check(
        "database.schema",
        ready,
        description="  Verify the deployed schema.  ",
        timeout=3,
    )

    assert declared.name == "database.schema"
    assert declared.description == "Verify the deployed schema."
    assert declared.timeout == 3.0
    assert declared.failure_code == "PREFLIGHT_DATABASE_SCHEMA_FAILED"


@pytest.mark.parametrize(
    "name",
    ["Bad", "bad-name", ".bad", "bad.", "bad..name"],
)
def test_preflight_names_use_dotted_snake_case(name: str) -> None:
    with pytest.raises(PreflightBindingError, match="dotted snake_case"):
        preflight_check(name, ready)


@pytest.mark.parametrize("timeout", [0, -1, float("inf"), float("nan"), True])
def test_preflight_timeouts_are_positive_finite_numbers(timeout: object) -> None:
    with pytest.raises(ConfigurationError, match="finite number greater than zero"):
        preflight_check("ready", ready, timeout=timeout)  # type: ignore[arg-type]


def test_preflight_failure_codes_are_static_identifiers() -> None:
    for code in ("", "secret value"):
        with pytest.raises(PreflightBindingError, match="SCREAMING_SNAKE_CASE"):
            preflight_check("ready", ready, failure_code=code)


def test_preflight_rejects_checks_that_cannot_honor_the_boundary() -> None:
    def sync() -> None:
        return None

    async def with_argument(value: object) -> None:
        del value

    async def with_result() -> str:
        return "unsafe"

    async def without_annotation():  # type: ignore[no-untyped-def]
        return None

    with pytest.raises(PreflightBindingError, match="async function"):
        preflight_check("sync", sync)  # type: ignore[arg-type]
    with pytest.raises(PreflightBindingError, match="accept no arguments"):
        preflight_check("argument", with_argument)  # type: ignore[arg-type]
    with pytest.raises(PreflightBindingError, match="must return None"):
        preflight_check("result", with_result)  # type: ignore[arg-type]
    with pytest.raises(PreflightBindingError, match="return annotation"):
        preflight_check("annotation", without_annotation)


def test_preflight_groups_flatten_and_reject_duplicate_names() -> None:
    first = preflight_check("one", ready)
    second = preflight_check("two", ready)

    assert preflight_group(first, preflight_group(second)).checks == (first, second)
    with pytest.raises(ConfigurationError, match="duplicate check name 'one'"):
        preflight_group(first, first)


async def test_preflight_runs_concurrently_and_reports_in_declaration_order() -> None:
    first_started = asyncio.Event()
    second_started = asyncio.Event()
    release = asyncio.Event()

    async def first() -> None:
        first_started.set()
        await release.wait()

    async def second() -> None:
        second_started.set()
        await release.wait()

    pending = asyncio.create_task(
        run_preflight(
            preflight_group(
                preflight_check("first", first),
                preflight_check("second", second),
            )
        )
    )
    await asyncio.gather(first_started.wait(), second_started.wait())

    release.set()
    report = await pending

    assert report.ok
    assert [outcome.name for outcome in report.outcomes] == ["first", "second"]


async def test_preflight_discards_exceptions_and_returned_values() -> None:
    secret = "database-password"

    async def raises() -> None:
        raise RuntimeError(secret)

    async def returns_value() -> None:
        return secret  # type: ignore[return-value]

    report = await run_preflight(
        preflight_group(
            preflight_check("raises", raises, failure_code="DEPENDENCY_FAILED"),
            preflight_check(
                "returns_value",
                returns_value,
                failure_code="UNSAFE_RESULT",
            ),
        )
    )

    assert not report.ok
    assert [outcome.status for outcome in report.outcomes] == ["failed", "failed"]
    assert [outcome.failure_code for outcome in report.outcomes] == [
        "DEPENDENCY_FAILED",
        "UNSAFE_RESULT",
    ]
    assert secret not in repr(report)


async def test_preflight_timeout_cap_cancels_and_cleans_up_the_check() -> None:
    cleaned_up = asyncio.Event()

    async def slow() -> None:
        try:
            await asyncio.Event().wait()
        finally:
            cleaned_up.set()

    report = await run_preflight(
        preflight_group(preflight_check("slow", slow, timeout=60)),
        timeout=0.01,
    )

    assert report.outcomes[0].status == "timed_out"
    assert report.outcomes[0].failure_code == "PREFLIGHT_SLOW_FAILED"
    assert cleaned_up.is_set()


async def test_preflight_cannot_pass_after_swallowing_its_deadline() -> None:
    async def swallows_deadline() -> None:
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            return None

    report = await run_preflight(
        preflight_group(preflight_check("slow", swallows_deadline)),
        timeout=0.01,
    )

    assert report.outcomes[0].status == "timed_out"


async def test_preflight_distinguishes_dependency_timeout_from_its_own_deadline() -> (
    None
):
    async def dependency_timeout() -> None:
        raise TimeoutError("dependency client timed out")

    report = await run_preflight(
        preflight_group(preflight_check("dependency", dependency_timeout))
    )

    assert report.outcomes[0].status == "failed"


async def test_preflight_timeout_cap_cannot_extend_a_declared_deadline() -> None:
    async def slow() -> None:
        await asyncio.Event().wait()

    report = await run_preflight(
        preflight_group(preflight_check("slow", slow, timeout=0.01)),
        timeout=60,
    )

    assert report.outcomes[0].status == "timed_out"


async def test_preflight_cancellation_propagates_to_active_checks() -> None:
    started = asyncio.Event()
    cleaned_up = asyncio.Event()

    async def wait() -> None:
        started.set()
        try:
            await asyncio.Event().wait()
        finally:
            cleaned_up.set()

    pending = asyncio.create_task(
        run_preflight(preflight_group(preflight_check("wait", wait)))
    )
    await started.wait()
    pending.cancel()

    with pytest.raises(asyncio.CancelledError):
        await pending
    assert cleaned_up.is_set()


async def test_empty_preflight_is_successful() -> None:
    report = await run_preflight(preflight_group())

    assert report.ok
    assert report.outcomes == ()
