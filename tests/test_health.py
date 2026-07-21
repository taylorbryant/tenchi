import asyncio
from dataclasses import dataclass

from tenchi.errors import ERROR_SOURCE_HEADER
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
