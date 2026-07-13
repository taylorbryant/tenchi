"""Route-to-use-case binding.

A route pairs one contract with one use case. Binding is checked eagerly:
:func:`route` inspects the use case signature and fails at import time when
the function cannot accept what the contract declares, so wiring mistakes
never wait for a request to surface.

Use cases are plain async functions. The server calls them with keyword
arguments derived from the contract: ``request`` when the contract declares
a request type, ``params`` when it declares path parameters, and always
``context``.
"""

from __future__ import annotations

import inspect
from collections.abc import Awaitable, Callable, Iterator, Sequence
from dataclasses import dataclass, replace
from typing import Any

from .contracts import Contract, ResponseT

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
    if contract.request is not None:
        call_kwargs.append("request")
    call_kwargs.append("context")

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
) -> RouteGroup:
    """Compose routes and route groups into one flat group.

    ``prefix`` is prepended to every contained contract path, so feature
    groups can be mounted under a common segment in ``server/routes.py``.
    """
    if prefix and not prefix.startswith("/"):
        raise ValueError(f"route_group prefix must start with '/', got {prefix!r}")

    flattened: list[Route] = []
    for item in items:
        if isinstance(item, Route):
            flattened.append(item)
        elif isinstance(item, RouteGroup):
            flattened.extend(item.routes)
        else:
            flattened.extend(item)

    if prefix:
        flattened = [
            replace(
                item, contract=replace(item.contract, path=prefix + item.contract.path)
            )
            for item in flattened
        ]

    return RouteGroup(routes=tuple(flattened))


def _describe(use_case: object) -> str:
    name = getattr(use_case, "__qualname__", None) or repr(use_case)
    module = getattr(use_case, "__module__", None)
    return f"{module}.{name}" if module else str(name)
