"""Tenchi: a contract-first, Python-native application framework.

Canonical imports use the submodules — ``tenchi.contracts``,
``tenchi.routes``, ``tenchi.errors``, ``tenchi.server``, ``tenchi.client``
— and the most common names are re-exported here for convenience.
"""

from .client import Client, UnexpectedResponseError
from .contracts import Contract, contract
from .errors import AppError, ErrorDef
from .routes import Route, RouteGroup, route, route_group
from .server import create_app

__version__ = "0.1.0"

__all__ = [
    "AppError",
    "Client",
    "Contract",
    "ErrorDef",
    "Route",
    "RouteGroup",
    "UnexpectedResponseError",
    "__version__",
    "contract",
    "create_app",
    "route",
    "route_group",
]
