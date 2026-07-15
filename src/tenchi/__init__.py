"""Tenchi: a contract-first, Python-native application framework.

Canonical imports use the submodules — ``tenchi.contracts``,
``tenchi.routes``, ``tenchi.errors``, ``tenchi.server``, ``tenchi.client``
— and the most common names are re-exported here for convenience.
"""

from .client import Client, UnexpectedResponseError
from .contracts import Contract, contract
from .errors import AppError, ErrorDef
from .execution import ExecutionError, execute
from .health import health_route
from .openapi import openapi_route, openapi_schema
from .pagination import Page, PageQuery, page
from .routes import Route, RouteGroup, route, route_group
from .server import RequestInfo, create_app

__version__ = "0.6.0"

__all__ = [
    "AppError",
    "Client",
    "Contract",
    "ErrorDef",
    "ExecutionError",
    "Page",
    "PageQuery",
    "RequestInfo",
    "Route",
    "RouteGroup",
    "UnexpectedResponseError",
    "__version__",
    "contract",
    "create_app",
    "execute",
    "health_route",
    "openapi_route",
    "openapi_schema",
    "page",
    "route",
    "route_group",
]
