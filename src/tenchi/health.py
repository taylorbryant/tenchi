"""Health checks served through Tenchi's own route machinery.

Compose :func:`health_route` alongside the application's routes:

    routes = route_group(
        api_routes,
        health_route(checks={"database": database_ready}),
    )

Checks receive the request context (so they can reach ports) and signal
failure by raising. Checks run concurrently; synchronous checks run in worker
threads so they cannot block the event loop. A healthy service
returns 200 with per-check statuses; any failure returns the standard error
envelope with status 503 and the ``UNHEALTHY`` code. Failure details expose
only exception class names — full tracebacks go to the log.

The route declares ``public=True`` by default so authentication hooks can
exempt it through explicit contract metadata.
"""

from __future__ import annotations

import asyncio
import inspect
import logging
from collections.abc import Callable, Mapping
from math import isfinite
from typing import Any, cast

from pydantic import BaseModel

from .contracts import contract
from .errors import AppError, ConfigurationError, ErrorDef
from .routes import Route, route

logger = logging.getLogger("tenchi.health")

unhealthy = ErrorDef(
    code="UNHEALTHY",
    status=503,
    message="Service unhealthy",
)

HealthCheck = Callable[[Any], Any]
"""A health check: ``(context) -> None``, sync or async; raise to fail."""


class HealthReport(BaseModel):
    status: str = "ok"
    checks: dict[str, str] = {}


def health_route(
    *,
    path: str = "/health",
    checks: Mapping[str, HealthCheck] | None = None,
    check_timeout: float = 5.0,
    public: bool = True,
) -> Route:
    """Build a route reporting service health.

    With no ``checks`` the route is a plain liveness endpoint. Each check
    receives the request context; a check that raises marks the service
    unhealthy and its exception class name (never the message) appears in
    the response details. A check that exceeds ``check_timeout`` seconds fails
    as ``TimeoutError`` — a hung dependency must produce the
    503 the contract promises, not a hung health endpoint. The route declares
    ``public=True`` by default; pass ``public=False`` when authentication hooks
    should protect it.
    """
    raw_timeout = cast(object, check_timeout)
    if (
        not isinstance(raw_timeout, int | float)
        or isinstance(raw_timeout, bool)
        or not isfinite(raw_timeout)
        or raw_timeout <= 0
    ):
        raise ConfigurationError(
            "health_route: check_timeout must be a finite number greater than zero"
        )
    raw_checks = cast(object, checks)
    if raw_checks is None:
        registered: dict[str, HealthCheck] = {}
    elif not isinstance(raw_checks, Mapping):
        raise ConfigurationError("health_route: checks must be a mapping")
    else:
        registered = {}
        for raw_name, check in cast(Mapping[object, object], raw_checks).items():
            name = raw_name
            if not isinstance(name, str) or not name.strip():
                raise ConfigurationError(
                    "health_route: check names must be non-empty strings"
                )
            if not callable(check):
                raise ConfigurationError(
                    f"health_route: check {name!r} must be callable"
                )
            registered[name] = cast(HealthCheck, check)

    health_contract = contract(
        method="GET",
        path=path,
        response=HealthReport,
        errors=(unhealthy,),
        summary="Service health",
        tags=("health",),
        public=public,
    )

    detached_checks: set[asyncio.Task[Any]] = set()

    def detach_check(task: asyncio.Task[Any]) -> None:
        """Keep an uncooperative timed-out check alive until it finishes."""
        detached_checks.add(task)

        def finished(completed: asyncio.Task[Any]) -> None:
            detached_checks.discard(completed)
            if completed.cancelled():
                return
            try:
                completed.exception()
            except BaseException:
                # Retrieving the exception prevents asyncio's unhandled-task
                # warning. The request path already logged a redacted timeout.
                return

        task.add_done_callback(finished)

    async def get_health(context: Any) -> HealthReport:
        async def invoke_check(check: HealthCheck) -> None:
            outcome = (
                check(context)
                if inspect.iscoroutinefunction(check)
                else await asyncio.to_thread(check, context)
            )
            if inspect.isawaitable(outcome):
                await outcome

        async def run_check(name: str, check: HealthCheck) -> tuple[str, str, bool]:
            task = asyncio.create_task(invoke_check(check))
            try:
                done, _ = await asyncio.wait(
                    (task,),
                    timeout=float(check_timeout),
                )
                if not done:
                    task.cancel()
                    detach_check(task)
                    raise TimeoutError
                await task
                return name, "ok", False
            except asyncio.CancelledError:
                if not task.done():
                    task.cancel()
                    detach_check(task)
                raise
            except BaseException as exc:
                logger.exception("Health check %r failed", name)
                return name, f"failed: {type(exc).__name__}", True

        outcomes = await asyncio.gather(
            *(run_check(name, check) for name, check in registered.items())
        )
        results = {name: status for name, status, _ in outcomes}
        failed = any(failed for _, _, failed in outcomes)
        if failed:
            raise AppError(unhealthy, details={"checks": results})
        return HealthReport(checks=results)

    return route(health_contract, get_health)
