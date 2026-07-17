"""Tenchi: a contract-first, Python-native application framework.

Canonical imports use the submodules — ``tenchi.contracts``,
``tenchi.routes``, ``tenchi.errors``, ``tenchi.server``, ``tenchi.client``
— and the most common names are re-exported here for convenience.
"""

from .client import Client, ClientResponse, UnexpectedResponseError
from .contracts import Contract, contract
from .errors import AppError, ConfigurationError, ErrorDef, TenchiError
from .execution import ExecutionError, execute
from .health import health_route
from .openapi import openapi_route, openapi_schema
from .pagination import Page, PageQuery, page
from .responses import PresentedResponse, SuccessDef, present, success
from .routes import Route, RouteGroup, route, route_group
from .server import OutcomeObserver, RequestInfo, RequestOutcome, create_app

__version__ = "0.8.0"

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
    "Route",
    "RouteGroup",
    "SuccessDef",
    "TenchiError",
    "UnexpectedResponseError",
    "__version__",
    "contract",
    "create_app",
    "execute",
    "health_route",
    "openapi_route",
    "openapi_schema",
    "page",
    "present",
    "route",
    "route_group",
    "success",
]
