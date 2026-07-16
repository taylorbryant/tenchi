"""Route-to-use-case binding.

A route pairs one contract with one use case. Binding is checked eagerly:
:func:`route` inspects the use case signature and fails at import time when
the function cannot accept what the contract declares, so wiring mistakes
never wait for a request to surface.

Use cases are plain async functions. The server calls them with keyword
arguments derived from the contract: ``request`` when the contract declares
a request type, ``params``/``query``/``headers`` when it declares those
input types, and always ``context``. Boundary parameter annotations and the
return annotation must exactly match the contract's declarations; the app-owned
``context`` annotation is not resolved or compared.
"""

from __future__ import annotations

import functools
import inspect
from collections.abc import Awaitable, Callable, Iterator, Sequence
from dataclasses import dataclass, replace
from typing import Any, cast

from pydantic import TypeAdapter

from .contracts import (
    _PATH_PARAMETER,  # pyright: ignore[reportPrivateUsage]
    Contract,
    ResponseHeadersT,
    ResponseT,
    _object_schema,  # pyright: ignore[reportPrivateUsage]
)
from .errors import (
    ConfigurationError,
    ErrorDef,
    _validated_error_defs,  # pyright: ignore[reportPrivateUsage]
)

UseCase = Callable[..., Awaitable[Any]]

_BOUNDARY_ARGUMENTS = ("params", "query", "headers", "request")


class RouteBindingError(ConfigurationError, TypeError):
    """Raised when a use case cannot satisfy its contract."""


@dataclass(frozen=True, slots=True)
class Route:
    """One contract bound to one use case."""

    contract: Contract[Any, Any]
    use_case: UseCase
    call_kwargs: tuple[str, ...]
    """Keyword arguments the server passes when invoking the use case."""
    response_headers: Callable[[Any], Any] | None = None
    """Pure projection from the validated result to declared success headers."""


@dataclass(frozen=True, slots=True)
class RouteGroup:
    """A flat, ordered collection of routes."""

    routes: tuple[Route, ...]

    def __iter__(self) -> Iterator[Route]:
        return iter(self.routes)

    def __len__(self) -> int:
        return len(self.routes)


def route(
    contract: Contract[ResponseT, ResponseHeadersT],
    use_case: Callable[..., Awaitable[ResponseT]],
    *,
    response_headers: Callable[[ResponseT], ResponseHeadersT] | None = None,
) -> Route:
    """Bind a contract to a use case, validating its signature and types."""
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

    try:
        signature = inspect.signature(use_case)
    except (TypeError, ValueError) as exc:
        raise RouteBindingError(
            f"route({contract.name!r}): could not inspect use case "
            f"{_describe(use_case)}: {exc}"
        ) from exc
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

    _check_type_coherence(contract, use_case, signature, accepts_any_kwargs)
    _check_response_headers_projector(contract, response_headers)

    return Route(
        contract=contract,
        use_case=use_case,
        call_kwargs=tuple(call_kwargs),
        response_headers=response_headers,
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
    prefix = _validated_prefix(prefix)
    declared_errors = _validated_error_defs(errors, label="route_group errors")
    if prefix and not prefix.startswith("/"):
        raise ConfigurationError(
            f"route_group prefix must start with '/', got {prefix!r}"
        )
    if prefix.endswith("/"):
        # Contract paths start with "/", so a trailing slash would build
        # double-slash paths that never match their intended URL.
        raise ConfigurationError(
            f"route_group prefix must not end with '/', got {prefix!r}"
        )

    flattened: list[Route] = []
    for index, item in enumerate(items):
        _append_routes(flattened, item, index=index)

    if prefix or declared_errors:
        flattened = [
            replace(item, contract=_amend(item.contract, prefix, declared_errors))
            for item in flattened
        ]

    return RouteGroup(routes=tuple(flattened))


def _amend(
    contract: Contract[Any, Any], prefix: str, errors: Sequence[ErrorDef]
) -> Contract[Any, Any]:
    merged = _validated_error_defs(
        (*contract.errors, *errors), label=f"route_group({contract.name!r}) errors"
    )
    path = prefix + contract.path if prefix else contract.path
    default_name = f"{contract.method} {contract.path}"
    name = (
        f"{contract.method} {path}" if contract.name == default_name else contract.name
    )
    return replace(
        contract,
        path=path,
        name=name,
        errors=merged,
    )


def _validated_prefix(value: object) -> str:
    if not isinstance(value, str):
        raise ConfigurationError(
            f"route_group prefix must be a string, got {type(value).__name__}"
        )
    return value


def _append_routes(flattened: list[Route], value: object, *, index: int) -> None:
    if isinstance(value, Route):
        flattened.append(value)
        return
    if isinstance(value, RouteGroup):
        flattened.extend(value.routes)
        return
    if isinstance(value, Sequence) and not isinstance(value, str | bytes):
        for nested_index, nested in enumerate(cast(Sequence[object], value)):
            if not isinstance(nested, Route):
                raise ConfigurationError(
                    f"route_group item[{index}][{nested_index}] must be a Route, "
                    f"got {type(nested).__name__}"
                )
            flattened.append(nested)
        return
    raise ConfigurationError(
        f"route_group item[{index}] must be a Route, RouteGroup, or sequence "
        f"of Route values, got {type(value).__name__}"
    )


def _check_params_match_path(contract: Contract[Any, Any]) -> None:
    """Fail at import time when a params model and the path template
    disagree — such a route would 422 on every single request."""
    placeholders = {match.group(1) for match in _PATH_PARAMETER.finditer(contract.path)}
    if contract.params is None:
        if placeholders:
            raise RouteBindingError(
                f"route({contract.name!r}): path declares parameters "
                f"{sorted(placeholders)} but the contract has no params type"
            )
        return
    params_type: Any = contract.params
    try:
        adapter = TypeAdapter(params_type)
        if not adapter.pydantic_complete:
            adapter.rebuild(raise_errors=True)
        schema = adapter.json_schema(mode="validation")
    except Exception as exc:
        raise RouteBindingError(
            f"route({contract.name!r}): could not inspect params type "
            f"{_type_name(params_type)}: {exc}"
        ) from exc
    object_schema = _object_schema(schema)
    if object_schema is None:
        raise RouteBindingError(
            f"route({contract.name!r}): params type {_type_name(params_type)} must "
            "describe object-shaped input"
        )
    fields = set(object_schema.get("properties", {}))
    if fields != placeholders:
        raise RouteBindingError(
            f"route({contract.name!r}): params model fields {sorted(fields)} "
            f"do not match path template parameters {sorted(placeholders)}"
        )


def _check_type_coherence(
    contract: Contract[Any, Any],
    use_case: UseCase,
    signature: inspect.Signature,
    accepts_any_kwargs: bool,
) -> None:
    """Require the contract and use-case boundary annotations to agree.

    The server validates values using the contract's types, then passes them
    to the use case. Exact agreement prevents the two declarations from
    drifting. The context annotation is deliberately ignored: it is app-owned
    and commonly imported only under ``TYPE_CHECKING``.
    """
    parameters = signature.parameters
    for name in _BOUNDARY_ARGUMENTS:
        contract_type = getattr(contract, name)
        parameter = parameters.get(name)
        if contract_type is None:
            if parameter is not None:
                raise RouteBindingError(
                    f"route({contract.name!r}): contract declares no {name!r} "
                    f"input, but use case {_describe(use_case)} has a {name!r} "
                    "parameter"
                )
            continue
        if parameter is None:
            assert accepts_any_kwargs
            raise RouteBindingError(
                f"route({contract.name!r}): use case {_describe(use_case)} must "
                f"declare an explicit annotated {name!r} parameter; **kwargs "
                "cannot prove the contract type agrees"
            )
        if parameter.annotation is inspect.Parameter.empty:
            raise RouteBindingError(
                f"route({contract.name!r}): use case {_describe(use_case)} "
                f"parameter {name!r} must be annotated with the contract type"
            )
        annotation = _resolve_annotation(
            contract, use_case, parameter.annotation, f"parameter {name!r}"
        )
        if annotation != contract_type:
            raise RouteBindingError(
                f"route({contract.name!r}): use case {_describe(use_case)} "
                f"parameter {name!r} annotation {_type_name(annotation)} does not "
                f"match contract {name} type {_type_name(contract_type)}"
            )

    if signature.return_annotation is inspect.Signature.empty:
        raise RouteBindingError(
            f"route({contract.name!r}): use case {_describe(use_case)} return value "
            "must be annotated with the contract response type"
        )
    annotation = _resolve_annotation(
        contract, use_case, signature.return_annotation, "return annotation"
    )
    if annotation != contract.response:
        raise RouteBindingError(
            f"route({contract.name!r}): use case {_describe(use_case)} return "
            f"annotation {_type_name(annotation)} does not match contract response "
            f"type {_type_name(contract.response)}"
        )


def _check_response_headers_projector(
    contract: Contract[Any, Any],
    projector: Callable[[Any], Any] | None,
) -> None:
    declared = contract.response_headers
    if declared is None:
        if projector is not None:
            raise RouteBindingError(
                f"route({contract.name!r}): response_headers= was provided but "
                "the contract declares no response_headers type"
            )
        return
    if projector is None:
        raise RouteBindingError(
            f"route({contract.name!r}): contract declares response_headers="
            f"{_type_name(declared)}; pass a typed response_headers= projector"
        )
    if inspect.iscoroutinefunction(projector):
        raise RouteBindingError(
            f"route({contract.name!r}): response_headers projector "
            f"{_describe(projector)} must be a synchronous function"
        )
    try:
        signature = inspect.signature(projector)
    except (TypeError, ValueError) as exc:
        raise RouteBindingError(
            f"route({contract.name!r}): could not inspect response_headers "
            f"projector {_describe(projector)}: {exc}"
        ) from exc
    parameters = tuple(signature.parameters.values())
    if len(parameters) != 1 or parameters[0].kind not in (
        inspect.Parameter.POSITIONAL_ONLY,
        inspect.Parameter.POSITIONAL_OR_KEYWORD,
    ):
        raise RouteBindingError(
            f"route({contract.name!r}): response_headers projector "
            f"{_describe(projector)} must accept exactly one positional result"
        )
    parameter = parameters[0]
    if parameter.annotation is inspect.Parameter.empty:
        raise RouteBindingError(
            f"route({contract.name!r}): response_headers projector "
            f"{_describe(projector)} result parameter must be annotated"
        )
    input_annotation = _resolve_annotation(
        contract, projector, parameter.annotation, "result parameter"
    )
    if input_annotation != contract.response:
        raise RouteBindingError(
            f"route({contract.name!r}): response_headers projector "
            f"{_describe(projector)} parameter annotation "
            f"{_type_name(input_annotation)} does not match contract response "
            f"type {_type_name(contract.response)}"
        )
    if signature.return_annotation is inspect.Signature.empty:
        raise RouteBindingError(
            f"route({contract.name!r}): response_headers projector "
            f"{_describe(projector)} return value must be annotated"
        )
    return_annotation = _resolve_annotation(
        contract, projector, signature.return_annotation, "return annotation"
    )
    if return_annotation != declared:
        raise RouteBindingError(
            f"route({contract.name!r}): response_headers projector "
            f"{_describe(projector)} return annotation "
            f"{_type_name(return_annotation)} does not match contract "
            f"response_headers type {_type_name(declared)}"
        )


def _resolve_annotation(
    contract: Contract[Any, Any],
    use_case: Callable[..., Any],
    annotation: Any,
    location: str,
) -> Any:
    function: Any = use_case
    while isinstance(function, functools.partial):
        function = function.func
    function = inspect.unwrap(function)
    namespace = getattr(function, "__globals__", {})
    original = annotation
    seen: set[str] = set()
    while isinstance(annotation, str):
        if annotation in seen:
            raise RouteBindingError(
                f"route({contract.name!r}): could not resolve use case "
                f"{_describe(use_case)} {location} {original!r}: cyclic forward "
                "reference"
            )
        seen.add(annotation)
        try:
            annotation = eval(annotation, namespace)
        except Exception as exc:
            raise RouteBindingError(
                f"route({contract.name!r}): could not resolve use case "
                f"{_describe(use_case)} {location} {original!r}: {exc}"
            ) from exc
    return annotation


def _type_name(annotation: Any) -> str:
    if annotation is None:
        return "None"
    return getattr(annotation, "__name__", repr(annotation))


def _describe(use_case: object) -> str:
    name = getattr(use_case, "__qualname__", None) or repr(use_case)
    module = getattr(use_case, "__module__", None)
    return f"{module}.{name}" if module else str(name)
