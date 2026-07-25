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
from .openapi import openapi_route, openapi_schema, swagger_ui_route
from .pagination import Page, PageQuery, page
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
    "AppError",
    "Client",
    "ClientResponse",
    "ConfigurationError",
    "Contract",
    "ErrorDef",
    "ExecutionError",
    "OutcomeObserver",
    "Page",
    "PageQuery",
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
    "health_route",
    "openapi_route",
    "openapi_schema",
    "page",
    "present",
    "response",
    "route",
    "route_group",
    "swagger_ui_route",
    "task",
    "task_group",
]
