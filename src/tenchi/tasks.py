"""Validated operational entrypoints for maintenance work.

Tasks bind stable names to the same plain async use cases used by HTTP routes,
workers, and tests. A task runner adds the application lifecycle around one
invocation and validates both sides of the call:

- input is validated before the lifespan or request-scoped context opens;
- the use case runs with an application context;
- output is validated before the context commits;
- use-case observers run after the context closes.

Tasks deliberately do not schedule, retry, queue, lock, or persist progress.
Those are deployment concerns; this module is the small, explicit boundary an
operator or agent can invoke.
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
from typing import Any, cast

from pydantic import TypeAdapter

from .errors import ConfigurationError, TenchiError
from .execution import (
    UseCaseObserver,
    _notify_use_case_observers,  # pyright: ignore[reportPrivateUsage]
    _UseCaseCall,  # pyright: ignore[reportPrivateUsage]
    _validated_observers,  # pyright: ignore[reportPrivateUsage]
    open_context,
)

_TASK_NAME = re.compile(r"^[a-z][a-z0-9_]*(?:\.[a-z][a-z0-9_]*)*$")


class TaskBindingError(ConfigurationError, TypeError):
    """A task declaration cannot safely invoke its use case."""


class TaskNotFoundError(TenchiError, LookupError):
    """No task with the requested stable name is registered."""


class TaskResultError(TenchiError, TypeError):
    """A task returned a value that violates its declared result type."""


class _Unset:
    def __repr__(self) -> str:
        return "UNSET"


_UNSET = _Unset()


@dataclass(frozen=True, slots=True)
class Task:
    """One named operational task bound to a plain async use case."""

    name: str
    use_case: Callable[..., Awaitable[Any]]
    description: str | None
    request: Any = field(repr=False)
    result: Any = field(repr=False)
    request_required: bool
    _request_adapter: TypeAdapter[Any] | None = field(
        init=False, repr=False, compare=False, hash=False
    )
    _result_adapter: TypeAdapter[Any] = field(
        init=False, repr=False, compare=False, hash=False
    )

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "_request_adapter",
            (
                None
                if self.request is _UNSET
                else _build_adapter(self.name, self.request, label="request")
            ),
        )
        object.__setattr__(
            self,
            "_result_adapter",
            _build_adapter(self.name, self.result, label="return"),
        )


@dataclass(frozen=True, slots=True)
class TaskGroup:
    """A flat, ordered collection of uniquely named tasks."""

    tasks: tuple[Task, ...]

    def __iter__(self) -> Iterator[Task]:
        return iter(self.tasks)

    def __len__(self) -> int:
        return len(self.tasks)


def task(
    name: str,
    use_case: Callable[..., Awaitable[Any]],
    *,
    description: str | None = None,
) -> Task:
    """Bind a stable operational name to an async use case.

    Names use dotted snake case, such as ``"projects.backfill_slugs"``.
    The use case may accept an annotated ``request`` and must accept
    ``context``. Its return annotation is the task's validated result
    contract.
    """
    if not _valid_task_name(name):
        raise TaskBindingError(
            "task name must use dotted snake_case, such as "
            f"'projects.backfill_slugs'; got {name!r}"
        )
    if description is not None and not _valid_description(description):
        raise TaskBindingError(
            f"task({name!r}): description must be a non-empty string or None"
        )

    signature = _task_signature(name, use_case)
    parameters = signature.parameters
    declared_request = parameters.get("request")
    _validate_use_case_shape(name, use_case, signature)

    request_annotation: Any = _UNSET
    request_required = False
    if declared_request is not None:
        request_annotation = _resolved_annotation(
            name,
            use_case,
            declared_request.annotation,
            label="request",
        )
        request_required = declared_request.default is inspect.Parameter.empty

    result_annotation = _resolved_annotation(
        name,
        use_case,
        signature.return_annotation,
        label="return",
    )
    return Task(
        name=name,
        use_case=use_case,
        description=description.strip() if description is not None else None,
        request=request_annotation,
        result=result_annotation,
        request_required=request_required,
    )


def task_group(*items: Task | TaskGroup | Sequence[Task]) -> TaskGroup:
    """Compose task declarations into one flat group with unique names."""
    flattened: list[Task] = []
    for index, item in enumerate(items):
        _append_tasks(flattened, item, index=index)

    seen: set[str] = set()
    for item in flattened:
        if item.name in seen:
            raise ConfigurationError(
                f"task_group contains duplicate task name {item.name!r}"
            )
        seen.add(item.name)
    return TaskGroup(tasks=tuple(flattened))


@dataclass(frozen=True, slots=True)
class TaskRunner:
    """Application lifecycle and context wiring for a task group."""

    tasks: TaskGroup
    context_factory: Callable[..., Any] = field(repr=False)
    lifespan: Callable[[], AbstractAsyncContextManager[Any]] | None = field(
        default=None, repr=False
    )
    use_case_observers: tuple[UseCaseObserver, ...] = field(default=(), repr=False)
    _context_takes_state: bool = field(init=False, default=False, repr=False)

    async def run(
        self,
        name: str,
        *,
        input: Any = _UNSET,
        input_json: bytes | str | None = None,
    ) -> Any:
        """Run one named task and return its validated Python result."""
        selected = next((item for item in self.tasks if item.name == name), None)
        if selected is None:
            raise TaskNotFoundError(f"unknown task {name!r}")
        kwargs = _task_kwargs(selected, input=input, input_json=input_json)

        async with _open_lifespan(self.lifespan) as state:
            context_source = (
                functools.partial(self.context_factory, state)
                if self._context_takes_state
                else self.context_factory
            )
            call = (
                _UseCaseCall(selected.use_case, entrypoint="task")
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
                    return _validated_result(selected, raw)
            finally:
                if call is not None:
                    await _notify_use_case_observers(
                        self.use_case_observers,
                        call,
                    )


def create_task_runner(
    *,
    tasks: TaskGroup,
    context_factory: Callable[..., Any],
    lifespan: Callable[[], AbstractAsyncContextManager[Any]] | None = None,
    use_case_observers: Sequence[UseCaseObserver] = (),
) -> TaskRunner:
    """Compose validated tasks with application lifecycle and context wiring."""
    _require_task_group(tasks)
    takes_state = _context_factory_takes_state(context_factory)
    if takes_state and lifespan is None:
        raise ConfigurationError(
            "create_task_runner: context_factory accepts a lifespan state "
            "argument but no lifespan= was provided"
        )
    if lifespan is not None:
        _require_call_shape(
            lifespan,
            positional_arguments=0,
            label="create_task_runner: lifespan",
            expectation="accept zero arguments",
        )
    try:
        observers = _validated_observers(use_case_observers)
    except Exception as exc:
        raise ConfigurationError(f"create_task_runner: {exc}") from exc
    runner = TaskRunner(
        tasks=tasks,
        context_factory=context_factory,
        lifespan=lifespan,
        use_case_observers=observers,
    )
    object.__setattr__(runner, "_context_takes_state", takes_state)
    return runner


def _task_signature(
    name: str, use_case: Callable[..., Awaitable[Any]]
) -> inspect.Signature:
    if not inspect.iscoroutinefunction(use_case):
        raise TaskBindingError(
            f"task({name!r}): use case {_describe(use_case)} must be an async function"
        )
    try:
        return inspect.signature(use_case)
    except (TypeError, ValueError) as exc:
        raise TaskBindingError(
            f"task({name!r}): could not inspect use case {_describe(use_case)}: {exc}"
        ) from exc


def _valid_task_name(value: object) -> bool:
    return isinstance(value, str) and _TASK_NAME.fullmatch(value) is not None


def _valid_description(value: object) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _validate_use_case_shape(
    name: str,
    use_case: Callable[..., Awaitable[Any]],
    signature: inspect.Signature,
) -> None:
    parameters = signature.parameters
    accepts_any_kwargs = any(
        parameter.kind is inspect.Parameter.VAR_KEYWORD
        for parameter in parameters.values()
    )
    for argument in ("request", "context"):
        parameter = parameters.get(argument)
        if parameter is not None and parameter.kind not in (
            inspect.Parameter.POSITIONAL_OR_KEYWORD,
            inspect.Parameter.KEYWORD_ONLY,
        ):
            raise TaskBindingError(
                f"task({name!r}): use case {_describe(use_case)} parameter "
                f"{argument!r} must be addressable by keyword"
            )
    if "context" not in parameters and not accepts_any_kwargs:
        raise TaskBindingError(
            f"task({name!r}): use case {_describe(use_case)} must accept "
            "a 'context' argument"
        )
    for parameter in parameters.values():
        if parameter.kind in (
            inspect.Parameter.VAR_KEYWORD,
            inspect.Parameter.VAR_POSITIONAL,
        ):
            continue
        if (
            parameter.name not in ("request", "context")
            and parameter.default is inspect.Parameter.empty
        ):
            raise TaskBindingError(
                f"task({name!r}): use case {_describe(use_case)} has required "
                f"parameter {parameter.name!r}; tasks only pass 'request' "
                "and 'context'"
            )


def _append_tasks(flattened: list[Task], item: object, *, index: int) -> None:
    if isinstance(item, Task):
        flattened.append(item)
        return
    if isinstance(item, TaskGroup):
        flattened.extend(item.tasks)
        return
    if isinstance(item, Sequence) and not isinstance(item, str | bytes):
        sequence = cast(Sequence[object], item)
        for nested_index, nested in enumerate(sequence):
            if not isinstance(nested, Task):
                raise ConfigurationError(
                    f"task_group item {index}[{nested_index}] must be a Task, "
                    f"got {type(nested).__name__}"
                )
            flattened.append(nested)
        return
    raise ConfigurationError(
        f"task_group item {index} must be a Task, TaskGroup, or "
        f"sequence of Task values, got {type(item).__name__}"
    )


def _require_task_group(value: object) -> None:
    if not isinstance(value, TaskGroup):
        raise ConfigurationError(
            f"create_task_runner: tasks must be a TaskGroup, got {type(value).__name__}"
        )


def _resolved_annotation(
    name: str,
    use_case: Callable[..., Awaitable[Any]],
    annotation: Any,
    *,
    label: str,
) -> Any:
    if annotation is inspect.Signature.empty:
        raise TaskBindingError(
            f"task({name!r}): use case {_describe(use_case)} {label} must be annotated"
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
            raise TaskBindingError(
                f"task({name!r}): could not resolve {label} annotation "
                f"{original!r}: cyclic forward reference"
            )
        seen.add(annotation)
        try:
            annotation = eval(annotation, namespace)
        except Exception as exc:
            raise TaskBindingError(
                f"task({name!r}): could not resolve {label} annotation "
                f"{original!r}: {exc}"
            ) from exc
    return annotation


def _build_adapter(name: str, annotation: Any, *, label: str) -> TypeAdapter[Any]:
    try:
        adapter = TypeAdapter(annotation)
        adapter.json_schema(
            mode="validation" if label == "request" else "serialization"
        )
        return adapter
    except Exception as exc:
        raise TaskBindingError(
            f"task({name!r}): Pydantic cannot use {label} annotation "
            f"{_type_name(annotation)}: {exc}"
        ) from exc


def _task_kwargs(
    item: Task,
    *,
    input: Any,
    input_json: bytes | str | None,
) -> dict[str, Any]:
    if input is not _UNSET and input_json is not None:
        raise TaskBindingError(
            f"task {item.name!r}: pass input= or input_json=, not both"
        )
    supplied = input is not _UNSET or input_json is not None
    adapter = item._request_adapter  # pyright: ignore[reportPrivateUsage]
    if adapter is None:
        if supplied:
            raise TaskBindingError(
                f"task {item.name!r} does not declare a request input"
            )
        return {}
    if not supplied:
        if not item.request_required:
            return {}
        raise TaskBindingError(f"task {item.name!r} requires input")
    if input_json is not None:
        return {"request": adapter.validate_json(input_json)}
    return {"request": adapter.validate_python(input)}


def _validated_result(item: Task, raw: Any) -> Any:
    try:
        validated = item._result_adapter.validate_python(  # pyright: ignore[reportPrivateUsage]
            raw
        )
        json_value = item._result_adapter.dump_python(  # pyright: ignore[reportPrivateUsage]
            validated,
            mode="json",
            by_alias=True,
            warnings="error",
        )
        # Reject NaN and infinities: Python's encoder accepts them, JSON does not.
        json.dumps(json_value, allow_nan=False)
    except (CancelledError, KeyboardInterrupt):
        raise
    except Exception as exc:
        raise TaskResultError(
            f"task {item.name!r} returned a value that does not match "
            f"{_type_name(item.result)}"
        ) from exc
    return validated


def _task_input_schema(  # pyright: ignore[reportUnusedFunction]
    item: Task,
) -> dict[str, Any] | None:
    adapter = item._request_adapter  # pyright: ignore[reportPrivateUsage]
    return (
        None
        if adapter is None
        else adapter.json_schema(mode="validation", by_alias=True)
    )


def _task_output_schema(  # pyright: ignore[reportUnusedFunction]
    item: Task,
) -> dict[str, Any]:
    return item._result_adapter.json_schema(  # pyright: ignore[reportPrivateUsage]
        mode="serialization", by_alias=True
    )


def _task_json_result(  # pyright: ignore[reportUnusedFunction]
    item: Task, value: Any
) -> Any:
    return item._result_adapter.dump_python(  # pyright: ignore[reportPrivateUsage]
        value,
        mode="json",
        by_alias=True,
        warnings="error",
    )


def _context_factory_takes_state(context_factory: Callable[..., Any]) -> bool:
    signature = _callable_signature(
        context_factory, label="create_task_runner: context_factory"
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
            "create_task_runner: context_factory must accept zero arguments or "
            f"a single positional lifespan state argument: {exc}"
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
            "create_task_runner: lifespan must return an async context manager"
        )
    scoped = cast(AbstractAsyncContextManager[Any], manager)
    async with scoped as state:
        yield state


def _describe(value: object) -> str:
    name = getattr(value, "__qualname__", None) or repr(value)
    module = getattr(value, "__module__", None)
    return f"{module}.{name}" if module else str(name)


def _type_name(annotation: Any) -> str:
    return getattr(annotation, "__name__", repr(annotation))


__all__ = [
    "Task",
    "TaskBindingError",
    "TaskGroup",
    "TaskNotFoundError",
    "TaskResultError",
    "TaskRunner",
    "create_task_runner",
    "task",
    "task_group",
]
