"""Health checks served through Tenchi's own route machinery.

Compose :func:`health_route` alongside the application's routes:

    routes = route_group(
        api_routes,
        health_route(checks={"database": database_ready}),
    )

Checks receive the request context (so they can reach ports) and signal
failure by raising. Asynchronous checks run concurrently. A healthy service
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
from typing import Any

from pydantic import BaseModel

from .contracts import contract
from .errors import AppError, ErrorDef
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
    the response details. An async check that exceeds ``check_timeout``
    seconds fails as ``TimeoutError`` — a hung dependency must produce the
    503 the contract promises, not a hung health endpoint. The route declares
    ``public=True`` by default; pass ``public=False`` when authentication hooks
    should protect it.
    """
    health_contract = contract(
        method="GET",
        path=path,
        response=HealthReport,
        errors=(unhealthy,),
        summary="Service health",
        tags=("health",),
        public=public,
    )
    registered = dict(checks or {})

    async def get_health(context: Any) -> HealthReport:
        async def run_check(name: str, check: HealthCheck) -> tuple[str, str, bool]:
            try:
                outcome = check(context)
                if inspect.isawaitable(outcome):
                    await asyncio.wait_for(_ensure_future(outcome), check_timeout)
                return name, "ok", False
            except Exception as exc:
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


def _ensure_future(outcome: Any) -> Any:
    """``asyncio.wait_for`` needs a real awaitable; wrap plain coroutines."""
    return asyncio.ensure_future(outcome)
