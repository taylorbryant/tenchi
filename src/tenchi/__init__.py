"""Tenchi: a contract-first, Python-native application framework.

Canonical imports use the submodules — ``tenchi.contracts``,
``tenchi.routes``, ``tenchi.errors``, ``tenchi.server``, ``tenchi.client``
— and the most common names are re-exported here for convenience.
"""

from .client import Client, ClientResponse, UnexpectedResponseError
from .contracts import Contract, contract
from .errors import AppError, ConfigurationError, ErrorDef, TenchiError
from .execution import ExecutionError, UseCaseObserver, UseCaseOutcome, execute
from .health import health_route
from .idempotency import (
    IDEMPOTENCY_CONFLICT,
    IDEMPOTENCY_IN_PROGRESS,
    IdempotencyResultError,
    IdempotencyStore,
    IdempotencyStoreError,
    fingerprint,
    run_idempotently,
)
from .openapi import openapi_route, openapi_schema, swagger_ui_route
from .pagination import Page, PageQuery, page
from .preflight import (
    PreflightBindingError,
    PreflightCheck,
    PreflightGroup,
    PreflightOutcome,
    PreflightReport,
    PreflightStatus,
    preflight_check,
    preflight_group,
    run_preflight,
)
from .responses import PresentedResponse, ResponseDef, present, response
from .routes import Route, RouteGroup, route, route_group
from .server import OutcomeObserver, RequestInfo, RequestOutcome, create_app
from .tasks import (
    Task,
    TaskBindingError,
    TaskGroup,
    TaskNotFoundError,
    TaskResultError,
    TaskRunner,
    create_task_runner,
    task,
    task_group,
)

__version__ = "0.11.0"

__all__ = [
    "IDEMPOTENCY_CONFLICT",
    "IDEMPOTENCY_IN_PROGRESS",
    "AppError",
    "Client",
    "ClientResponse",
    "ConfigurationError",
    "Contract",
    "ErrorDef",
    "ExecutionError",
    "IdempotencyResultError",
    "IdempotencyStore",
    "IdempotencyStoreError",
    "OutcomeObserver",
    "Page",
    "PageQuery",
    "PreflightBindingError",
    "PreflightCheck",
    "PreflightGroup",
    "PreflightOutcome",
    "PreflightReport",
    "PreflightStatus",
    "PresentedResponse",
    "RequestInfo",
    "RequestOutcome",
    "ResponseDef",
    "Route",
    "RouteGroup",
    "Task",
    "TaskBindingError",
    "TaskGroup",
    "TaskNotFoundError",
    "TaskResultError",
    "TaskRunner",
    "TenchiError",
    "UnexpectedResponseError",
    "UseCaseObserver",
    "UseCaseOutcome",
    "__version__",
    "contract",
    "create_app",
    "create_task_runner",
    "execute",
    "fingerprint",
    "health_route",
    "openapi_route",
    "openapi_schema",
    "page",
    "preflight_check",
    "preflight_group",
    "present",
    "response",
    "route",
    "route_group",
    "run_idempotently",
    "run_preflight",
    "swagger_ui_route",
    "task",
    "task_group",
]
