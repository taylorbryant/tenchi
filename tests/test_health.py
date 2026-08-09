import asyncio
import time
from dataclasses import dataclass

import pytest

from tenchi.errors import ERROR_SOURCE_HEADER, ConfigurationError
from tenchi.health import health_route
from tenchi.routes import route_group
from tenchi.server import create_app
from tenchi.testing import open_http


@dataclass(frozen=True, slots=True)
class Context:
    database_ok: bool = True


def make_app(
    *,
    checks: dict[str, object] | None = None,
    ok: bool = True,
    check_timeout: float = 5.0,
):
    return create_app(
        routes=route_group(
            health_route(
                checks=checks,  # pyright: ignore[reportArgumentType]
                check_timeout=check_timeout,
            )
        ),
        context_factory=lambda: Context(database_ok=ok),
    )


async def test_health_without_checks_is_a_liveness_endpoint() -> None:
    async with open_http(make_app()) as http:
        response = await http.get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok", "checks": {}}


async def test_healthy_checks_report_ok() -> None:
    def sync_check(context: Context) -> None:
        assert context.database_ok

    async def async_check(context: Context) -> None:
        assert context.database_ok

    app = make_app(checks={"sync": sync_check, "async": async_check})

    async with open_http(app) as http:
        response = await http.get("/health")

    assert response.status_code == 200
    assert response.json() == {
        "status": "ok",
        "checks": {"sync": "ok", "async": "ok"},
    }


async def test_async_health_checks_run_concurrently() -> None:
    database_started = asyncio.Event()
    cache_started = asyncio.Event()

    async def database(context: Context) -> None:
        assert context.database_ok
        database_started.set()
        await cache_started.wait()

    async def cache(context: Context) -> None:
        assert context.database_ok
        cache_started.set()
        await database_started.wait()

    app = make_app(checks={"database": database, "cache": cache}, check_timeout=0.1)

    async with open_http(app) as http:
        response = await http.get("/health")

    assert response.status_code == 200
    assert response.json()["checks"] == {"database": "ok", "cache": "ok"}


async def test_failing_check_maps_to_503_without_leaking_messages() -> None:
    def database(context: Context) -> None:
        if not context.database_ok:
            raise ConnectionError("secret host details")

    def cache(context: Context) -> None:
        return None

    app = make_app(checks={"database": database, "cache": cache}, ok=False)

    async with open_http(app) as http:
        response = await http.get("/health")

    assert response.status_code == 503
    assert response.headers[ERROR_SOURCE_HEADER] == "app"
    body = response.json()
    assert body["code"] == "UNHEALTHY"
    assert body["details"]["checks"] == {
        "database": "failed: ConnectionError",
        "cache": "ok",
    }
    assert "secret host details" not in response.text


async def test_health_route_is_public_and_tagged_for_documentation() -> None:
    assert health_route().contract.public is True
    assert "health" in health_route().contract.tags


def test_health_route_can_require_authentication() -> None:
    assert health_route(public=False).contract.public is False


async def test_custom_path() -> None:
    app = create_app(
        routes=route_group(health_route(path="/status")),
        context_factory=Context,
    )

    async with open_http(app) as http:
        assert (await http.get("/status")).status_code == 200


async def test_hung_check_times_out_to_503() -> None:
    async def hangs(context: object) -> None:
        await asyncio.sleep(60)

    app = create_app(
        routes=route_group(
            health_route(checks={"upstream": hangs}, check_timeout=0.05)
        ),
        context_factory=Context,
    )

    async with open_http(app) as http:
        response = await http.get("/health")

    assert response.status_code == 503
    assert response.json()["details"]["checks"]["upstream"] == "failed: TimeoutError"


async def test_check_cannot_defeat_deadline_by_suppressing_cancellation() -> None:
    cancelled = asyncio.Event()
    release = asyncio.Event()
    finished = asyncio.Event()

    async def suppresses_cancellation(context: object) -> None:
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            cancelled.set()
            await release.wait()
            finished.set()

    app = make_app(
        checks={"upstream": suppresses_cancellation},
        check_timeout=0.01,
    )

    async with open_http(app) as http:
        response = await asyncio.wait_for(http.get("/health"), timeout=0.1)

    assert response.status_code == 503
    assert response.json()["details"]["checks"]["upstream"] == ("failed: TimeoutError")
    await asyncio.wait_for(cancelled.wait(), timeout=0.1)
    release.set()
    await asyncio.wait_for(finished.wait(), timeout=0.1)


async def test_blocking_sync_check_does_not_block_the_health_deadline() -> None:
    def blocks(context: object) -> None:
        time.sleep(0.1)

    app = make_app(checks={"upstream": blocks}, check_timeout=0.01)
    started = time.monotonic()

    async with open_http(app) as http:
        response = await http.get("/health")

    assert time.monotonic() - started < 0.08
    assert response.status_code == 503
    assert response.json()["details"]["checks"]["upstream"] == "failed: TimeoutError"


async def test_base_exception_from_check_is_a_redacted_health_failure() -> None:
    class FatalCheckFailure(BaseException):
        pass

    def fails(context: object) -> None:
        raise FatalCheckFailure("do not expose me")

    app = make_app(checks={"upstream": fails})

    async with open_http(app) as http:
        response = await http.get("/health")

    assert response.status_code == 503
    assert response.json()["details"]["checks"]["upstream"] == (
        "failed: FatalCheckFailure"
    )
    assert "do not expose me" not in response.text


@pytest.mark.parametrize("timeout", [0.0, -1.0, float("inf"), float("nan"), True])
def test_health_route_rejects_invalid_timeouts(timeout: float) -> None:
    with pytest.raises(ConfigurationError, match="finite number greater than zero"):
        health_route(check_timeout=timeout)


def test_health_route_rejects_malformed_check_registries() -> None:
    with pytest.raises(ConfigurationError, match="checks must be a mapping"):
        health_route(checks=[])  # pyright: ignore[reportArgumentType]
    with pytest.raises(ConfigurationError, match="non-empty strings"):
        health_route(checks={"": lambda context: None})
    with pytest.raises(ConfigurationError, match="must be callable"):
        health_route(checks={"database": object()})  # pyright: ignore[reportArgumentType]
