"""Contract declarations for the HTTP boundary.

A contract is pure data: method, path, the types validated at the boundary,
the success status, and the application errors the route is allowed to
return. Validation itself happens in the server, which builds Pydantic
``TypeAdapter`` instances from these declarations, so any type Pydantic can
validate works as a request, params, or response type — models, dataclasses,
``list[Model]``, and so on.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any, Generic

from typing_extensions import TypeVar

from .errors import ErrorDef

ResponseT = TypeVar("ResponseT", default=Any)

_METHODS = frozenset({"GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS"})


@dataclass(frozen=True)
class Contract(Generic[ResponseT]):
    """One declared HTTP operation.

    Build these with :func:`contract` rather than instantiating directly.
    """

    method: str
    path: str
    request: type[Any] | None = None
    params: type[Any] | None = None
    query: type[Any] | None = None
    response: type[ResponseT] | None = None
    status: int = 200
    errors: tuple[ErrorDef, ...] = ()
    name: str = field(default="")

    def declares_error(self, definition: ErrorDef) -> bool:
        """Whether this contract declares the given error as expected."""
        return definition in self.errors


def contract(
    *,
    method: str,
    path: str,
    request: type[Any] | None = None,
    params: type[Any] | None = None,
    query: type[Any] | None = None,
    response: type[ResponseT] | None = None,
    status: int = 200,
    errors: Sequence[ErrorDef] = (),
    name: str | None = None,
) -> Contract[ResponseT]:
    """Declare an HTTP contract.

    Args:
        method: HTTP method, such as ``"POST"``.
        path: Route path. Path parameters use ``{name}`` segments, for
            example ``"/todos/{todo_id}"``.
        request: Type validated from the JSON request body. The validated
            value is passed to the use case as its ``request`` argument.
        params: Type validated from the path parameters, usually a Pydantic
            model with one field per ``{name}`` segment. Passed to the use
            case as its ``params`` argument.
        query: Type validated from the URL query string, usually a Pydantic
            model whose fields have defaults. Passed to the use case as its
            ``query`` argument. Values arrive as strings (or lists of
            strings for repeated keys) and are coerced by Pydantic.
        response: Type the use case result is validated against before
            serialization. ``None`` means an empty response body.
        status: Success status code. Defaults to 200.
        errors: Application errors this route is expected to return. Expected
            errors are mapped to their HTTP status; undeclared ``AppError``
            instances are treated as internal server errors.
        name: Optional stable name, defaulting to ``"METHOD path"``.
    """
    normalized_method = method.upper()
    if normalized_method not in _METHODS:
        raise ValueError(f"contract(path={path!r}): unsupported HTTP method {method!r}")
    if not path.startswith("/"):
        raise ValueError(f"contract path must start with '/', got {path!r}")
    if not 100 <= status <= 599:
        raise ValueError(f"contract(path={path!r}): invalid status {status}")
    return Contract(
        method=normalized_method,
        path=path,
        request=request,
        params=params,
        query=query,
        response=response,
        status=status,
        errors=tuple(errors),
        name=name or f"{normalized_method} {path}",
    )
