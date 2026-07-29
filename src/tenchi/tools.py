"""Typed application capabilities for AI and machine callers.

Tools give an application use case a stable, discoverable machine-facing
contract without choosing an agent SDK or transport. Declarations own names,
input and output schemas, expected application errors, and conservative safety
annotations. Bindings connect those declarations to plain async use cases at
composition time.

The runner applies the same boundary guarantees as HTTP, jobs, and operational
tasks:

- input is validated before the application lifespan or context opens;
- output is validated before the scoped context commits;
- declared application errors may cross the boundary;
- undeclared application errors and unexpected failures become one generic
  :class:`ToolInvocationError`;
- use-case observers run after context cleanup and never receive payloads.

Model selection, tool loops, prompts, approval UX, authentication, and transport
protocols remain outside this module.
"""

from __future__ import annotations

import functools
import inspect
import json
import re
from asyncio import CancelledError
from collections.abc import AsyncGenerator, Awaitable, Callable, Iterator, Sequence
from contextlib import AbstractAsyncContextManager, asynccontextmanager
from dataclasses import dataclass, field
from typing import Any, Generic, Literal, TypeVar, cast, overload

from jsonschema import Draft202012Validator, FormatChecker
from jsonschema.protocols import Validator
from pydantic import ConfigDict, TypeAdapter, ValidationError, with_config
from typing_extensions import TypedDict, TypeForm

from .errors import (
    AppError,
    ConfigurationError,
    ErrorDef,
    TenchiError,
    _validated_error_defs,  # pyright: ignore[reportPrivateUsage]
)
from .execution import (
    UseCaseObserver,
    _notify_use_case_observers,  # pyright: ignore[reportPrivateUsage]
    _UseCaseCall,  # pyright: ignore[reportPrivateUsage]
    _validated_observers,  # pyright: ignore[reportPrivateUsage]
    open_context,
)

_TOOL_NAME = re.compile(r"^[a-z][a-z0-9_]*(?:\.[a-z][a-z0-9_]*)*$")
TOOL_MANIFEST_VERSION = 1
# Explicit TypeVars keep both parameters invariant. PEP 695 would infer
# variance for these runtime-only declaration types and let a mismatched
# handler widen the result type during generic inference.
_ToolRequestT = TypeVar("_ToolRequestT")
_ToolResultT = TypeVar("_ToolResultT")


class _Unset:
    def __repr__(self) -> str:
        return "UNSET"


_UNSET = _Unset()


class ToolBindingError(ConfigurationError, TypeError):
    """A tool declaration and use case cannot be bound safely."""


class ToolNotFoundError(TenchiError, LookupError):
    """No registered tool has the requested stable name."""


class ToolResultError(TenchiError, TypeError):
    """A tool use case returned a value outside its declared result type."""


class ToolInvocationError(TenchiError, RuntimeError):
    """A tool failed without a declared application error.

    The message is deliberately generic and safe for a machine caller. The
    original exception remains available through ``__cause__`` for
    application-owned logging and debugging.
    """


class _ToolCallUsageError(ToolBindingError):
    """A safe caller mistake detected before input validation."""


class _ToolResultValidationError(ToolResultError):
    """A safe result-contract failure created by Tenchi itself."""


@with_config(ConfigDict(extra="forbid"))
class _EmptyToolInput(TypedDict):
    """The object-shaped input accepted by a no-input tool."""


_EMPTY_TOOL_INPUT_ADAPTER = TypeAdapter(_EmptyToolInput)


@dataclass(frozen=True, slots=True)
class Tool(Generic[_ToolRequestT, _ToolResultT]):  # noqa: UP046
    """One stable machine-facing capability declaration.

    Build tools with :func:`tool` instead of constructing this dataclass
    directly so conservative annotation defaults are applied.
    """

    name: str
    description: str
    request: Any = field(repr=False)
    result: Any = field(repr=False)
    errors: tuple[ErrorDef, ...]
    read_only: bool
    destructive: bool
    idempotent: bool
    open_world: bool
    _request_adapter: TypeAdapter[Any] | None = field(
        init=False, repr=False, compare=False, hash=False
    )
    _result_adapter: TypeAdapter[Any] = field(
        init=False, repr=False, compare=False, hash=False
    )
    _result_schema: dict[str, Any] = field(
        init=False, repr=False, compare=False, hash=False
    )
    _result_schema_validator: Validator = field(
        init=False, repr=False, compare=False, hash=False
    )

    def __post_init__(self) -> None:
        raw_name = cast(object, self.name)
        if not isinstance(raw_name, str) or _TOOL_NAME.fullmatch(raw_name) is None:
            raise ToolBindingError(
                "tool name must use dotted snake_case, such as "
                f"'projects.search'; got {self.name!r}"
            )
        raw_description = cast(object, self.description)
        if not isinstance(raw_description, str) or not raw_description.strip():
            raise ToolBindingError(
                f"tool({self.name!r}): description must be a non-empty string"
            )
        object.__setattr__(self, "description", raw_description.strip())
        object.__setattr__(
            self,
            "errors",
            _validated_error_defs(
                self.errors,
                label=f"tool({self.name!r}) errors",
            ),
        )
        for label, value in (
            ("read_only", self.read_only),
            ("destructive", self.destructive),
            ("idempotent", self.idempotent),
            ("open_world", self.open_world),
        ):
            if not isinstance(cast(object, value), bool):
                raise ToolBindingError(f"tool({self.name!r}): {label} must be a bool")
        if self.read_only and self.destructive:
            raise ToolBindingError(
                f"tool({self.name!r}): a read-only tool cannot be destructive"
            )
        if self.read_only and not self.idempotent:
            raise ToolBindingError(
                f"tool({self.name!r}): a read-only tool must be idempotent"
            )
        object.__setattr__(
            self,
            "_request_adapter",
            (
                None
                if self.request is None
                else _build_adapter(self.name, self.request, label="request")
            ),
        )
        result_adapter = _build_adapter(self.name, self.result, label="result")
        result_schema = result_adapter.json_schema(
            mode="serialization",
            by_alias=True,
        )
        object.__setattr__(self, "_result_adapter", result_adapter)
        object.__setattr__(self, "_result_schema", result_schema)
        object.__setattr__(
            self,
            "_result_schema_validator",
            _build_schema_validator(self.name, result_schema),
        )

    def declares_error(self, definition: ErrorDef) -> bool:
        """Whether this tool explicitly allows an application error."""
        return definition in self.errors


@overload
def tool[RequestT, ResultT](
    name: str,
    *,
    request: TypeForm[RequestT],
    result: TypeForm[ResultT],
    description: str,
    errors: Sequence[ErrorDef] = (),
    read_only: bool = False,
    destructive: bool | None = None,
    idempotent: bool | None = None,
    open_world: bool = True,
) -> Tool[RequestT, ResultT]: ...


@overload
def tool[RequestT](
    name: str,
    *,
    request: TypeForm[RequestT],
    result: None,
    description: str,
    errors: Sequence[ErrorDef] = (),
    read_only: bool = False,
    destructive: bool | None = None,
    idempotent: bool | None = None,
    open_world: bool = True,
) -> Tool[RequestT, None]: ...


@overload
def tool[ResultT](
    name: str,
    *,
    request: None = None,
    result: TypeForm[ResultT],
    description: str,
    errors: Sequence[ErrorDef] = (),
    read_only: bool = False,
    destructive: bool | None = None,
    idempotent: bool | None = None,
    open_world: bool = True,
) -> Tool[None, ResultT]: ...


@overload
def tool(
    name: str,
    *,
    request: None = None,
    result: None,
    description: str,
    errors: Sequence[ErrorDef] = (),
    read_only: bool = False,
    destructive: bool | None = None,
    idempotent: bool | None = None,
    open_world: bool = True,
) -> Tool[None, None]: ...


@overload
def tool(
    name: str,
    *,
    request: object | None = None,
    result: object | None,
    description: str,
    errors: Sequence[ErrorDef] = (),
    read_only: bool = False,
    destructive: bool | None = None,
    idempotent: bool | None = None,
    open_world: bool = True,
) -> Tool[Any, Any]: ...


def tool(
    name: str,
    *,
    request: object | None = None,
    result: object | None,
    description: str,
    errors: Sequence[ErrorDef] = (),
    read_only: bool = False,
    destructive: bool | None = None,
    idempotent: bool | None = None,
    open_world: bool = True,
) -> Tool[Any, Any]:
    """Declare a stable tool contract.

    Names use dotted ``snake_case``. Omitting ``request`` declares a tool with
    no input. Safety annotations default conservatively: write-capable tools
    are destructive unless explicitly narrowed, all read-only tools are
    idempotent, and tools are assumed to interact with an open world.
    """
    raw_read_only = _validated_bool(name, "read_only", read_only)
    raw_destructive = _validated_optional_bool(name, "destructive", destructive)
    raw_idempotent = _validated_optional_bool(name, "idempotent", idempotent)
    raw_open_world = _validated_bool(name, "open_world", open_world)
    resolved_destructive = (
        not raw_read_only if raw_destructive is None else raw_destructive
    )
    resolved_idempotent = raw_read_only if raw_idempotent is None else raw_idempotent
    return Tool(
        name=name,
        description=description,
        request=request,
        result=result,
        errors=tuple(errors),
        read_only=raw_read_only,
        destructive=resolved_destructive,
        idempotent=resolved_idempotent,
        open_world=raw_open_world,
    )


@dataclass(frozen=True, slots=True)
class ToolBinding[RequestT, ResultT]:
    """One tool declaration explicitly bound to a plain async use case."""

    tool: Tool[RequestT, ResultT]
    use_case: Callable[..., Awaitable[ResultT]] = field(repr=False)

    def __post_init__(self) -> None:
        _validate_tool_handler(self.tool, self.use_case)


def tool_handler[RequestT, ResultT](
    declaration: Tool[RequestT, ResultT],
    use_case: Callable[..., Awaitable[ResultT]],
) -> ToolBinding[RequestT, ResultT]:
    """Bind a tool declaration to an exactly annotated async use case."""
    return ToolBinding(tool=declaration, use_case=use_case)


def _validate_tool_handler(
    declaration: object,
    use_case: Callable[..., Awaitable[Any]],
) -> None:
    _require_tool(declaration, label="tool_handler")
    declaration = cast(Tool[Any, Any], declaration)
    signature = _handler_signature(declaration.name, use_case)
    _validate_handler_shape(declaration, use_case, signature)

    if declaration.request is not None:
        request_annotation = _resolved_annotation(
            declaration.name,
            use_case,
            signature.parameters["request"].annotation,
            label="request",
        )
        if request_annotation != declaration.request:
            raise ToolBindingError(
                f"tool_handler({declaration.name!r}): use case "
                f"{_describe(use_case)} request annotation "
                f"{_type_name(request_annotation)} does not match the tool "
                f"request {_type_name(declaration.request)}"
            )

    result_annotation = _resolved_annotation(
        declaration.name,
        use_case,
        signature.return_annotation,
        label="return",
    )
    if result_annotation != declaration.result:
        raise ToolBindingError(
            f"tool_handler({declaration.name!r}): use case "
            f"{_describe(use_case)} return annotation "
            f"{_type_name(result_annotation)} does not match the tool result "
            f"{_type_name(declaration.result)}"
        )


@dataclass(frozen=True, slots=True)
class ToolGroup:
    """A flat, ordered collection of uniquely named tool bindings."""

    handlers: tuple[ToolBinding[Any, Any], ...]

    def __post_init__(self) -> None:
        raw_handlers = cast(object, self.handlers)
        if not isinstance(raw_handlers, tuple):
            raise ConfigurationError(
                "ToolGroup handlers must be a tuple of ToolBinding values, "
                f"got {type(raw_handlers).__name__}"
            )
        seen: set[str] = set()
        for index, item in enumerate(cast(tuple[object, ...], raw_handlers)):
            if not isinstance(item, ToolBinding):
                raise ConfigurationError(
                    f"ToolGroup handlers[{index}] must be a ToolBinding, "
                    f"got {type(item).__name__}"
                )
            binding = cast(ToolBinding[Any, Any], item)
            name = binding.tool.name
            if name in seen:
                raise ConfigurationError(
                    f"tool_group contains duplicate tool name {name!r}"
                )
            seen.add(name)

    def __iter__(self) -> Iterator[ToolBinding[Any, Any]]:
        return iter(self.handlers)

    def __len__(self) -> int:
        return len(self.handlers)


def tool_group(
    *items: ToolBinding[Any, Any] | ToolGroup | Sequence[ToolBinding[Any, Any]],
) -> ToolGroup:
    """Compose tool bindings into one flat group with unique names."""
    flattened: list[ToolBinding[Any, Any]] = []
    for index, item in enumerate(items):
        _append_handlers(flattened, item, index=index)

    return ToolGroup(handlers=tuple(flattened))


@dataclass(frozen=True, slots=True)
class ToolRunner:
    """Application lifespan and context wiring for a registered tool group."""

    tools: ToolGroup
    context_factory: Callable[..., Any] = field(repr=False)
    lifespan: Callable[[], AbstractAsyncContextManager[Any]] | None = field(
        default=None,
        repr=False,
    )
    use_case_observers: tuple[UseCaseObserver, ...] = field(default=(), repr=False)
    _context_takes_state: bool = field(init=False, default=False, repr=False)

    async def call(
        self,
        name: str,
        *,
        input: Any = _UNSET,
        input_json: bytes | str | None = None,
    ) -> Any:
        """Invoke one named tool and return its validated Python result."""
        selected = next(
            (item for item in self.tools if item.tool.name == name),
            None,
        )
        if selected is None:
            raise ToolNotFoundError(f"unknown tool {name!r}")
        try:
            kwargs = _tool_kwargs(
                selected.tool,
                input=input,
                input_json=input_json,
            )
        except (KeyboardInterrupt, _ToolCallUsageError, ValidationError):
            raise
        except Exception as exc:
            raise ToolInvocationError(f"tool {name!r} failed") from exc

        try:
            async with _open_lifespan(self.lifespan) as state:
                context_source = (
                    functools.partial(self.context_factory, state)
                    if self._context_takes_state
                    else self.context_factory
                )
                call = (
                    _UseCaseCall(selected.use_case, entrypoint="tool")
                    if self.use_case_observers
                    else None
                )
                try:
                    async with open_context(context_source) as context:
                        raw = (
                            await selected.use_case(**kwargs, context=context)
                            if call is None
                            else await call.invoke(**kwargs, context=context)
                        )
                        return _validated_result(selected.tool, raw)
                finally:
                    if call is not None:
                        await _notify_use_case_observers(
                            self.use_case_observers,
                            call,
                        )
        except (CancelledError, KeyboardInterrupt, _ToolResultValidationError):
            raise
        except AppError as exc:
            if selected.tool.declares_error(exc.definition):
                raise
            raise ToolInvocationError(f"tool {name!r} failed") from exc
        except Exception as exc:
            raise ToolInvocationError(f"tool {name!r} failed") from exc


def create_tool_runner(
    *,
    tools: ToolGroup,
    context_factory: Callable[..., Any],
    lifespan: Callable[[], AbstractAsyncContextManager[Any]] | None = None,
    use_case_observers: Sequence[UseCaseObserver] = (),
) -> ToolRunner:
    """Compose registered tools with application lifecycle and context wiring."""
    _require_tool_group(tools)
    takes_state = _context_factory_takes_state(context_factory)
    if takes_state and lifespan is None:
        raise ConfigurationError(
            "create_tool_runner: context_factory accepts a lifespan state "
            "argument but no lifespan= was provided"
        )
    if lifespan is not None:
        _require_call_shape(
            lifespan,
            positional_arguments=0,
            label="create_tool_runner: lifespan",
            expectation="accept zero arguments",
        )
    try:
        observers = _validated_observers(use_case_observers)
    except Exception as exc:
        raise ConfigurationError(f"create_tool_runner: {exc}") from exc
    runner = ToolRunner(
        tools=tools,
        context_factory=context_factory,
        lifespan=lifespan,
        use_case_observers=observers,
    )
    object.__setattr__(runner, "_context_takes_state", takes_state)
    return runner


@with_config(ConfigDict(extra="forbid"))
class ToolAnnotationManifest(TypedDict):
    """Portable safety hints attached to a manifest tool."""

    read_only: bool
    destructive: bool
    idempotent: bool
    open_world: bool


@with_config(ConfigDict(extra="forbid"))
class ToolErrorManifest(TypedDict):
    """One declared, caller-visible application error."""

    code: str
    message: str


@with_config(ConfigDict(extra="forbid"))
class ToolManifestEntry(TypedDict):
    """One registered tool's portable discovery document."""

    name: str
    description: str
    input_schema: dict[str, Any]
    output_schema: dict[str, Any]
    errors: list[ToolErrorManifest]
    annotations: ToolAnnotationManifest


@with_config(ConfigDict(extra="forbid"))
class ToolManifest(TypedDict):
    """Versioned deterministic discovery document for a tool group."""

    schema_version: Literal[1]
    tools: list[ToolManifestEntry]


def tool_manifest(tools: ToolGroup) -> ToolManifest:
    """Return a canonical JSON-serializable manifest of registered tools."""
    _require_tool_group(tools)
    entries: list[ToolManifestEntry] = []
    for binding in sorted(tools, key=lambda item: item.tool.name):
        declaration = binding.tool
        request_adapter = (
            declaration._request_adapter  # pyright: ignore[reportPrivateUsage]
        )
        input_schema: dict[str, Any] = (
            _empty_input_schema()
            if request_adapter is None
            else request_adapter.json_schema(mode="validation", by_alias=True)
        )
        output_schema = (
            declaration._result_schema  # pyright: ignore[reportPrivateUsage]
        )
        entries.append(
            {
                "name": declaration.name,
                "description": declaration.description,
                "input_schema": input_schema,
                "output_schema": output_schema,
                "errors": [
                    {"code": error.code, "message": error.message}
                    for error in sorted(
                        declaration.errors,
                        key=lambda error: error.code,
                    )
                ],
                "annotations": {
                    "read_only": declaration.read_only,
                    "destructive": declaration.destructive,
                    "idempotent": declaration.idempotent,
                    "open_world": declaration.open_world,
                },
            }
        )
    payload: ToolManifest = {
        "schema_version": TOOL_MANIFEST_VERSION,
        "tools": entries,
    }
    return cast(
        ToolManifest,
        json.loads(
            json.dumps(
                payload,
                allow_nan=False,
                ensure_ascii=False,
                separators=(",", ":"),
                sort_keys=True,
            )
        ),
    )


def _validated_bool(name: str, label: str, value: object) -> bool:
    if not isinstance(value, bool):
        raise ToolBindingError(f"tool({name!r}): {label} must be a bool")
    return value


def _validated_optional_bool(
    name: str,
    label: str,
    value: object,
) -> bool | None:
    if value is not None and not isinstance(value, bool):
        raise ToolBindingError(f"tool({name!r}): {label} must be a bool or None")
    return value


def _build_adapter(name: str, annotation: Any, *, label: str) -> TypeAdapter[Any]:
    try:
        adapter = TypeAdapter(annotation)
        adapter.json_schema(
            mode="validation" if label == "request" else "serialization",
        )
        return adapter
    except Exception as exc:
        raise ToolBindingError(
            f"tool({name!r}): Pydantic cannot use {label} annotation "
            f"{_type_name(annotation)}: {exc}"
        ) from exc


def _build_schema_validator(
    name: str,
    schema: dict[str, Any],
) -> Validator:
    try:
        Draft202012Validator.check_schema(schema)
        return Draft202012Validator(
            schema,
            format_checker=FormatChecker(),
        )
    except Exception as exc:
        raise ToolBindingError(
            f"tool({name!r}): result serialization schema is invalid: {exc}"
        ) from exc


def _empty_input_schema() -> dict[str, Any]:
    schema = _EMPTY_TOOL_INPUT_ADAPTER.json_schema(mode="validation")
    schema.pop("title", None)
    schema.pop("description", None)
    return schema


def _handler_signature(
    name: str,
    use_case: Callable[..., Awaitable[Any]],
) -> inspect.Signature:
    if not inspect.iscoroutinefunction(use_case):
        raise ToolBindingError(
            f"tool_handler({name!r}): use case {_describe(use_case)} must be "
            "an async function"
        )
    try:
        return inspect.signature(use_case)
    except (TypeError, ValueError) as exc:
        raise ToolBindingError(
            f"tool_handler({name!r}): could not inspect use case "
            f"{_describe(use_case)}: {exc}"
        ) from exc


def _validate_handler_shape(
    declaration: Tool[Any, Any],
    use_case: Callable[..., Awaitable[Any]],
    signature: inspect.Signature,
) -> None:
    parameters = signature.parameters
    expected = {"context"}
    if declaration.request is not None:
        expected.add("request")
    for argument in expected:
        parameter = parameters.get(argument)
        if parameter is None:
            raise ToolBindingError(
                f"tool_handler({declaration.name!r}): use case "
                f"{_describe(use_case)} must accept a {argument!r} argument"
            )
        if parameter.kind not in (
            inspect.Parameter.POSITIONAL_OR_KEYWORD,
            inspect.Parameter.KEYWORD_ONLY,
        ):
            raise ToolBindingError(
                f"tool_handler({declaration.name!r}): use case "
                f"{_describe(use_case)} parameter {argument!r} must be "
                "addressable by keyword"
            )
    if declaration.request is None and "request" in parameters:
        raise ToolBindingError(
            f"tool_handler({declaration.name!r}): use case "
            f"{_describe(use_case)} accepts 'request' but the tool declares "
            "no request input"
        )
    unexpected = [
        parameter.name
        for parameter in parameters.values()
        if parameter.name not in expected
    ]
    if unexpected:
        joined = ", ".join(repr(name) for name in unexpected)
        raise ToolBindingError(
            f"tool_handler({declaration.name!r}): use case "
            f"{_describe(use_case)} has unsupported parameter(s) {joined}; "
            "tools only pass 'request' and 'context'"
        )


def _resolved_annotation(
    name: str,
    use_case: Callable[..., Awaitable[Any]],
    annotation: Any,
    *,
    label: str,
) -> Any:
    if annotation is inspect.Signature.empty:
        raise ToolBindingError(
            f"tool_handler({name!r}): use case {_describe(use_case)} {label} "
            "must be annotated"
        )
    function: Any = use_case
    while isinstance(function, functools.partial):
        function = function.func
    function = inspect.unwrap(function)
    namespace = getattr(function, "__globals__", {})
    original = annotation
    seen: set[str] = set()
    while isinstance(annotation, str):
        if annotation in seen:
            raise ToolBindingError(
                f"tool_handler({name!r}): could not resolve {label} annotation "
                f"{original!r}: cyclic forward reference"
            )
        seen.add(annotation)
        try:
            annotation = eval(annotation, namespace)
        except Exception as exc:
            raise ToolBindingError(
                f"tool_handler({name!r}): could not resolve {label} annotation "
                f"{original!r}: {exc}"
            ) from exc
    return annotation


def _tool_kwargs(
    declaration: Tool[Any, Any],
    *,
    input: Any,
    input_json: bytes | str | None,
) -> dict[str, Any]:
    if input is not _UNSET and input_json is not None:
        raise _ToolCallUsageError(
            f"tool {declaration.name!r}: pass input= or input_json=, not both"
        )
    supplied = input is not _UNSET or input_json is not None
    adapter = (
        declaration._request_adapter  # pyright: ignore[reportPrivateUsage]
    )
    if adapter is None:
        if input_json is not None:
            _EMPTY_TOOL_INPUT_ADAPTER.validate_json(input_json)
        elif input is not _UNSET:
            _EMPTY_TOOL_INPUT_ADAPTER.validate_python(input)
        return {}
    if not supplied:
        raise _ToolCallUsageError(f"tool {declaration.name!r} requires input")
    if input_json is not None:
        return {"request": adapter.validate_json(input_json)}
    return {"request": adapter.validate_python(input)}


def _validated_result(declaration: Tool[Any, Any], raw: Any) -> Any:
    try:
        adapter = (
            declaration._result_adapter  # pyright: ignore[reportPrivateUsage]
        )
        validated = adapter.validate_python(raw)
        json_value = adapter.dump_python(
            validated,
            mode="json",
            by_alias=True,
            warnings="error",
        )
        json.dumps(json_value, allow_nan=False)
        validator = (
            declaration._result_schema_validator  # pyright: ignore[reportPrivateUsage]
        )
        validator.validate(json_value)
    except (CancelledError, KeyboardInterrupt):
        raise
    except Exception as exc:
        raise _ToolResultValidationError(
            f"tool {declaration.name!r} returned a value that does not match "
            f"{_type_name(declaration.result)}"
        ) from exc
    return validated


def _append_handlers(
    flattened: list[ToolBinding[Any, Any]],
    item: object,
    *,
    index: int,
) -> None:
    if isinstance(item, ToolBinding):
        flattened.append(cast(ToolBinding[Any, Any], item))
        return
    if isinstance(item, ToolGroup):
        flattened.extend(item.handlers)
        return
    if isinstance(item, Sequence) and not isinstance(item, str | bytes):
        sequence = cast(Sequence[object], item)
        for nested_index, nested in enumerate(sequence):
            if not isinstance(nested, ToolBinding):
                raise ConfigurationError(
                    f"tool_group item {index}[{nested_index}] must be a "
                    f"ToolBinding, got {type(nested).__name__}"
                )
            flattened.append(cast(ToolBinding[Any, Any], nested))
        return
    raise ConfigurationError(
        f"tool_group item {index} must be a ToolBinding, ToolGroup, or "
        f"sequence of ToolBinding values, got {type(item).__name__}"
    )


def _require_tool(value: object, *, label: str) -> None:
    if not isinstance(value, Tool):
        raise ConfigurationError(
            f"{label}: declaration must be a Tool, got {type(value).__name__}"
        )


def _require_tool_group(value: object) -> None:
    if not isinstance(value, ToolGroup):
        raise ConfigurationError(
            f"tools must be a ToolGroup, got {type(value).__name__}"
        )


def _context_factory_takes_state(context_factory: Callable[..., Any]) -> bool:
    signature = _callable_signature(
        context_factory,
        label="create_tool_runner: context_factory",
    )
    try:
        signature.bind()
    except TypeError:
        pass
    else:
        return False
    try:
        signature.bind(object())
    except TypeError as exc:
        raise ConfigurationError(
            "create_tool_runner: context_factory must accept zero arguments "
            f"or a single positional lifespan state argument: {exc}"
        ) from exc
    return True


def _callable_signature(value: object, *, label: str) -> inspect.Signature:
    try:
        return inspect.signature(cast(Callable[..., object], value))
    except (TypeError, ValueError) as exc:
        raise ConfigurationError(
            f"{label} has no inspectable signature: {exc}"
        ) from exc


def _require_call_shape(
    value: object,
    *,
    positional_arguments: int,
    label: str,
    expectation: str,
) -> None:
    signature = _callable_signature(value, label=label)
    try:
        signature.bind(*(object() for _ in range(positional_arguments)))
    except TypeError as exc:
        raise ConfigurationError(f"{label} must {expectation}: {exc}") from exc


@asynccontextmanager
async def _open_lifespan(
    lifespan: Callable[[], object] | None,
) -> AsyncGenerator[Any]:
    if lifespan is None:
        yield None
        return
    manager = lifespan()
    if not isinstance(manager, AbstractAsyncContextManager):
        raise ConfigurationError(
            "create_tool_runner: lifespan must return an async context manager"
        )
    scoped = cast(AbstractAsyncContextManager[Any], manager)
    async with scoped as state:
        yield state


def _describe(value: object) -> str:
    name = getattr(value, "__qualname__", None) or repr(value)
    module = getattr(value, "__module__", None)
    return f"{module}.{name}" if module else str(name)


def _type_name(value: object) -> str:
    return getattr(value, "__name__", repr(value))


__all__ = [
    "TOOL_MANIFEST_VERSION",
    "Tool",
    "ToolAnnotationManifest",
    "ToolBinding",
    "ToolBindingError",
    "ToolErrorManifest",
    "ToolGroup",
    "ToolInvocationError",
    "ToolManifest",
    "ToolManifestEntry",
    "ToolNotFoundError",
    "ToolResultError",
    "ToolRunner",
    "create_tool_runner",
    "tool",
    "tool_group",
    "tool_handler",
    "tool_manifest",
]
