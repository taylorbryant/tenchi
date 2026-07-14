"""Route-to-use-case binding.

A route pairs one contract with one use case. Binding is checked eagerly:
:func:`route` inspects the use case signature and fails at import time when
the function cannot accept what the contract declares, so wiring mistakes
never wait for a request to surface.

Use cases are plain async functions. The server calls them with keyword
arguments derived from the contract: ``request`` when the contract declares
a request type, ``params``/``query``/``headers`` when it declares those
input types, and always ``context``.
"""

from __future__ import annotations

import inspect
import re
from collections.abc import Awaitable, Callable, Iterator, Sequence
from dataclasses import dataclass, replace
from typing import Any

from pydantic import BaseModel

from .contracts import Contract, ResponseT
from .errors import ErrorDef

UseCase = Callable[..., Awaitable[Any]]


class RouteBindingError(TypeError):
    """Raised when a use case cannot satisfy its contract."""


@dataclass(frozen=True, slots=True)
class Route:
    """One contract bound to one use case."""

    contract: Contract[Any]
    use_case: UseCase
    call_kwargs: tuple[str, ...]
    """Keyword arguments the server passes when invoking the use case."""


@dataclass(frozen=True, slots=True)
class RouteGroup:
    """A flat, ordered collection of routes."""

    routes: tuple[Route, ...]

    def __iter__(self) -> Iterator[Route]:
        return iter(self.routes)

    def __len__(self) -> int:
        return len(self.routes)


def route(
    contract: Contract[ResponseT],
    use_case: Callable[..., Awaitable[ResponseT]],
) -> Route:
    """Bind a contract to a use case, validating the signature eagerly."""
    if not inspect.iscoroutinefunction(use_case):
        raise RouteBindingError(
            f"route({contract.name!r}): use case "
            f"{_describe(use_case)} must be an async function"
        )

    call_kwargs: list[str] = []
    if contract.params is not None:
        call_kwargs.append("params")
    if contract.query is not None:
        call_kwargs.append("query")
    if contract.headers is not None:
        call_kwargs.append("headers")
    if contract.request is not None:
        call_kwargs.append("request")
    call_kwargs.append("context")

    _check_params_match_path(contract)

    signature = inspect.signature(use_case)
    parameters = signature.parameters
    accepts_any_kwargs = any(
        parameter.kind is inspect.Parameter.VAR_KEYWORD
        for parameter in parameters.values()
    )

    for kwarg in call_kwargs:
        parameter = parameters.get(kwarg)
        if parameter is None:
            if accepts_any_kwargs:
                continue
            raise RouteBindingError(
                f"route({contract.name!r}): use case {_describe(use_case)} "
                f"must accept a {kwarg!r} argument"
            )
        if parameter.kind not in (
            inspect.Parameter.POSITIONAL_OR_KEYWORD,
            inspect.Parameter.KEYWORD_ONLY,
        ):
            raise RouteBindingError(
                f"route({contract.name!r}): use case {_describe(use_case)} "
                f"parameter {kwarg!r} must be addressable by keyword"
            )

    expected = set(call_kwargs)
    for parameter in parameters.values():
        if parameter.kind in (
            inspect.Parameter.VAR_KEYWORD,
            inspect.Parameter.VAR_POSITIONAL,
        ):
            continue
        if parameter.name not in expected and parameter.default is (
            inspect.Parameter.empty
        ):
            raise RouteBindingError(
                f"route({contract.name!r}): use case {_describe(use_case)} "
                f"has required parameter {parameter.name!r} that the "
                f"contract does not provide; it only passes "
                f"{sorted(expected)}"
            )

    return Route(
        contract=contract,
        use_case=use_case,
        call_kwargs=tuple(call_kwargs),
    )


def route_group(
    *items: Route | RouteGroup | Sequence[Route],
    prefix: str = "",
    errors: Sequence[ErrorDef] = (),
) -> RouteGroup:
    """Compose routes and route groups into one flat group.

    ``prefix`` is prepended to every contained contract path, so feature
    groups can be mounted under a common segment in ``server/routes.py``.

    ``errors`` declares expected errors on every contained contract — the
    ergonomic way to declare errors an app-level hook may raise (such as an
    authentication failure) without repeating them contract by contract.
    Declarations are appended and deduplicated.
    """
    if prefix and not prefix.startswith("/"):
        raise ValueError(f"route_group prefix must start with '/', got {prefix!r}")
    if prefix.endswith("/"):
        # Contract paths start with "/", so a trailing slash would build
        # double-slash paths that never match their intended URL.
        raise ValueError(f"route_group prefix must not end with '/', got {prefix!r}")

    flattened: list[Route] = []
    for item in items:
        if isinstance(item, Route):
            flattened.append(item)
        elif isinstance(item, RouteGroup):
            flattened.extend(item.routes)
        else:
            flattened.extend(item)

    if prefix or errors:
        flattened = [
            replace(item, contract=_amend(item.contract, prefix, errors))
            for item in flattened
        ]

    return RouteGroup(routes=tuple(flattened))


def _amend(
    contract: Contract[Any], prefix: str, errors: Sequence[ErrorDef]
) -> Contract[Any]:
    merged = contract.errors + tuple(
        definition for definition in errors if definition not in contract.errors
    )
    return replace(
        contract,
        path=prefix + contract.path if prefix else contract.path,
        errors=merged,
    )


def _check_params_match_path(contract: Contract[Any]) -> None:
    """Fail at import time when a params model and the path template
    disagree — such a route would 422 on every single request."""
    placeholders = set(re.findall(r"{([^}:]+)[^}]*}", contract.path))
    if contract.params is None:
        if placeholders:
            raise RouteBindingError(
                f"route({contract.name!r}): path declares parameters "
                f"{sorted(placeholders)} but the contract has no params type"
            )
        return
    params_type: Any = contract.params
    if not inspect.isclass(params_type) or not issubclass(params_type, BaseModel):
        return  # non-model params types are validated only at runtime
    fields = set(params_type.model_fields)
    if fields != placeholders:
        raise RouteBindingError(
            f"route({contract.name!r}): params model fields {sorted(fields)} "
            f"do not match path template parameters {sorted(placeholders)}"
        )


def _describe(use_case: object) -> str:
    name = getattr(use_case, "__qualname__", None) or repr(use_case)
    module = getattr(use_case, "__module__", None)
    return f"{module}.{name}" if module else str(name)
