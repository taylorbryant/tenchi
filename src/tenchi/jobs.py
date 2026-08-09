"""Validated messages and handlers for application-owned background jobs.

Tenchi defines the boundary between a producer and consumer; it does not own a
queue. Producers serialize a declared :class:`Job` into a :class:`JobMessage`.
The composition root binds each declaration to a plain async use case, and a
:class:`JobDispatcher` validates both sides of delivery. Queue persistence,
claiming, acknowledgement, retry, backoff, and dead-lettering remain explicit
infrastructure decisions.
"""

from __future__ import annotations

import functools
import inspect
import json
import re
from asyncio import CancelledError
from collections.abc import Awaitable, Callable, Iterator, Sequence
from copy import deepcopy
from dataclasses import dataclass, field
from types import UnionType
from typing import Any, Literal, cast, overload

from jsonschema import Draft202012Validator, FormatChecker
from jsonschema.protocols import Validator
from pydantic import ConfigDict, TypeAdapter, with_config
from typing_extensions import TypedDict

from .errors import ConfigurationError, TenchiError
from .execution import (
    UseCaseObserver,
    _notify_use_case_observers,  # pyright: ignore[reportPrivateUsage]
    _UseCaseCall,  # pyright: ignore[reportPrivateUsage]
    _validated_observers,  # pyright: ignore[reportPrivateUsage]
    open_context,
)

_JOB_NAME = re.compile(r"^[a-z][a-z0-9_]*(?:\.[a-z][a-z0-9_]*)*$")
JOB_MANIFEST_VERSION = 1


@with_config(ConfigDict(extra="forbid"))
class JobManifestEntry(TypedDict):
    """One durable job message contract in a discovery manifest."""

    name: str
    description: str | None
    input_schema: dict[str, Any]


@with_config(ConfigDict(extra="forbid"))
class JobManifest(TypedDict):
    """Versioned deterministic discovery document for registered jobs."""

    schema_version: Literal[1]
    jobs: list[JobManifestEntry]


class JobBindingError(ConfigurationError, TypeError):
    """A job declaration and handler cannot be bound safely."""


class JobNotFoundError(TenchiError, LookupError):
    """No handler for the requested stable job name is registered."""


class JobResultError(TenchiError, TypeError):
    """A job handler returned a value outside its declared result type."""


@dataclass(frozen=True, slots=True)
class Job[RequestT, ResultT]:
    """A stable, typed message contract shared by producer and consumer."""

    name: str
    description: str | None
    request: Any = field(repr=False)
    result: Any = field(repr=False)
    _request_adapter: TypeAdapter[Any] = field(
        init=False, repr=False, compare=False, hash=False
    )
    _request_schema: dict[str, Any] = field(
        init=False, repr=False, compare=False, hash=False
    )
    _request_schema_validator: Validator = field(
        init=False, repr=False, compare=False, hash=False
    )
    _result_adapter: TypeAdapter[Any] = field(
        init=False, repr=False, compare=False, hash=False
    )

    def __post_init__(self) -> None:
        raw_name = cast(object, self.name)
        if not isinstance(raw_name, str) or _JOB_NAME.fullmatch(raw_name) is None:
            raise JobBindingError(
                "job name must use dotted snake_case, such as "
                f"'projects.member_added'; got {self.name!r}"
            )
        raw_description = cast(object, self.description)
        if raw_description is not None and (
            not isinstance(raw_description, str) or not raw_description.strip()
        ):
            raise JobBindingError(
                f"job({self.name!r}): description must be a non-empty string or None"
            )
        if isinstance(raw_description, str):
            object.__setattr__(self, "description", raw_description.strip())
        request_adapter = _build_adapter(self.name, self.request, label="request")
        request_schema, request_schema_validator = _request_schema(
            self.name,
            request_adapter,
        )
        object.__setattr__(self, "_request_adapter", request_adapter)
        object.__setattr__(self, "_request_schema", request_schema)
        object.__setattr__(
            self,
            "_request_schema_validator",
            request_schema_validator,
        )
        object.__setattr__(
            self,
            "_result_adapter",
            _build_adapter(self.name, self.result, label="result"),
        )


@overload
def job[RequestT, ResultT](
    name: str,
    *,
    request: type[RequestT] | UnionType,
    result: type[ResultT] | UnionType,
    description: str | None = None,
) -> Job[RequestT, ResultT]: ...


@overload
def job[RequestT](
    name: str,
    *,
    request: type[RequestT] | UnionType,
    result: None,
    description: str | None = None,
) -> Job[RequestT, None]: ...


def job(
    name: str,
    *,
    request: type[Any] | UnionType,
    result: type[Any] | UnionType | None,
    description: str | None = None,
) -> Job[Any, Any]:
    """Declare a stable background-job message and result contract."""
    return Job(
        name=name,
        description=description,
        request=request,
        result=result,
    )


@dataclass(frozen=True, slots=True)
class JobMessage:
    """A validated job name and compact JSON payload for queue storage."""

    name: str
    payload_json: bytes = field(repr=False)


def job_message[RequestT, ResultT](
    declaration: Job[RequestT, ResultT],
    request: RequestT,
) -> JobMessage:
    """Validate and serialize one producer message before it is enqueued."""
    _require_job(declaration, label="job_message")
    adapter = declaration._request_adapter  # pyright: ignore[reportPrivateUsage]
    validated = adapter.validate_python(request)
    payload_json = _serialized_job_payload(declaration, adapter, validated)
    if payload_json is None:
        raise JobBindingError(
            f"job_message({declaration.name!r}): serialized payload does not "
            "match the published input schema"
        )
    return JobMessage(
        name=declaration.name,
        payload_json=payload_json,
    )


def _serialized_job_payload(
    declaration: Job[Any, Any],
    adapter: TypeAdapter[Any],
    validated: Any,
) -> bytes | None:
    try:
        payload_json = adapter.dump_json(
            validated,
            by_alias=True,
            round_trip=True,
            warnings="error",
        )
        serialized = json.loads(payload_json)
        validator = declaration._request_schema_validator  # pyright: ignore[reportPrivateUsage]
        validator.validate(serialized)
        # Strict JSON validation prevents coercion from hiding a serializer
        # that emits a different wire type than the published input schema.
        adapter.validate_json(payload_json, strict=True)
    except (CancelledError, KeyboardInterrupt):
        raise
    except Exception:
        return None
    return payload_json


@dataclass(frozen=True, slots=True)
class JobHandler[RequestT, ResultT]:
    """One job declaration bound to its plain async consumer use case."""

    job: Job[RequestT, ResultT]
    use_case: Callable[..., Awaitable[ResultT]] = field(repr=False)

    def __post_init__(self) -> None:
        _validate_job_handler(self.job, self.use_case)


def job_handler[RequestT, ResultT](
    declaration: Job[RequestT, ResultT],
    use_case: Callable[..., Awaitable[ResultT]],
) -> JobHandler[RequestT, ResultT]:
    """Bind a job declaration to an exactly annotated async use case."""
    return JobHandler(job=declaration, use_case=use_case)


def _validate_job_handler(
    declaration: Job[Any, Any],
    use_case: Callable[..., Awaitable[Any]],
) -> None:
    _require_job(declaration, label="JobHandler")
    signature = _handler_signature(declaration.name, use_case)
    _validate_handler_shape(declaration.name, use_case, signature)
    request_parameter = signature.parameters["request"]
    request_annotation = _resolved_annotation(
        declaration.name,
        use_case,
        request_parameter.annotation,
        label="request",
    )
    result_annotation = _resolved_annotation(
        declaration.name,
        use_case,
        signature.return_annotation,
        label="return",
    )
    if request_annotation != declaration.request:
        raise JobBindingError(
            f"job_handler({declaration.name!r}): use case {_describe(use_case)} "
            f"request annotation {_type_name(request_annotation)} does not match "
            f"the job request {_type_name(declaration.request)}"
        )
    if result_annotation != declaration.result:
        raise JobBindingError(
            f"job_handler({declaration.name!r}): use case {_describe(use_case)} "
            f"return annotation {_type_name(result_annotation)} does not match "
            f"the job result {_type_name(declaration.result)}"
        )


@dataclass(frozen=True, slots=True)
class JobGroup:
    """A flat, ordered collection of uniquely named job handlers."""

    handlers: tuple[JobHandler[Any, Any], ...]

    def __post_init__(self) -> None:
        raw_handlers = cast(object, self.handlers)
        if not isinstance(raw_handlers, tuple):
            raise ConfigurationError("JobGroup.handlers must be a tuple")
        seen: set[str] = set()
        for index, item in enumerate(cast(tuple[object, ...], raw_handlers)):
            if not isinstance(item, JobHandler):
                raise ConfigurationError(
                    f"JobGroup.handlers[{index}] must be a JobHandler, got "
                    f"{type(item).__name__}"
                )
            binding = cast(JobHandler[Any, Any], item)
            if binding.job.name in seen:
                raise ConfigurationError(
                    f"job_group contains duplicate job name {binding.job.name!r}"
                )
            seen.add(binding.job.name)

    def __iter__(self) -> Iterator[JobHandler[Any, Any]]:
        return iter(self.handlers)

    def __len__(self) -> int:
        return len(self.handlers)


def job_group(
    *items: JobHandler[Any, Any] | JobGroup | Sequence[JobHandler[Any, Any]],
) -> JobGroup:
    """Compose job handlers into one flat group with unique names."""
    flattened: list[JobHandler[Any, Any]] = []
    for index, item in enumerate(items):
        _append_handlers(flattened, item, index=index)
    return JobGroup(handlers=tuple(flattened))


def job_manifest(jobs: JobGroup) -> JobManifest:
    """Return the canonical, payload-free message manifest for *jobs*.

    Only the producer-to-consumer message boundary is included. Handler result
    values are process-local acknowledgements and are not part of a durable
    queue message contract.
    """
    raw_jobs = cast(object, jobs)
    if not isinstance(raw_jobs, JobGroup):
        raise ConfigurationError(
            f"job_manifest: jobs must be a JobGroup, got {type(jobs).__name__}"
        )
    entries: list[JobManifestEntry] = []
    for binding in sorted(jobs, key=lambda item: item.job.name):
        declaration = binding.job
        entries.append(
            {
                "name": declaration.name,
                "description": declaration.description,
                "input_schema": deepcopy(
                    declaration._request_schema  # pyright: ignore[reportPrivateUsage]
                ),
            }
        )
    payload: JobManifest = {
        "schema_version": JOB_MANIFEST_VERSION,
        "jobs": entries,
    }
    return cast(
        JobManifest,
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


@dataclass(frozen=True, slots=True)
class JobDispatcher:
    """Validated consumer dispatch over an application-owned context scope."""

    jobs: JobGroup
    use_case_observers: tuple[UseCaseObserver, ...] = field(default=(), repr=False)

    def __post_init__(self) -> None:
        raw_jobs = cast(object, self.jobs)
        if not isinstance(raw_jobs, JobGroup):
            raise ConfigurationError(
                f"JobDispatcher.jobs must be a JobGroup, got {type(raw_jobs).__name__}"
            )
        try:
            observers = _validated_observers(self.use_case_observers)
        except Exception as exc:
            raise ConfigurationError(f"JobDispatcher: {exc}") from exc
        object.__setattr__(self, "use_case_observers", observers)

    async def dispatch(
        self,
        name: str,
        *,
        payload_json: bytes | str,
        context: Any,
    ) -> Any:
        """Validate and invoke one delivered job, returning its typed result."""
        selected = next(
            (item for item in self.jobs if item.job.name == name),
            None,
        )
        if selected is None:
            raise JobNotFoundError(f"unknown job {name!r}")
        request = selected.job._request_adapter.validate_json(  # pyright: ignore[reportPrivateUsage]
            payload_json
        )
        call = (
            _UseCaseCall(selected.use_case, entrypoint="job")
            if self.use_case_observers
            else None
        )
        try:
            async with open_context(context) as entered:
                raw = (
                    await selected.use_case(request=request, context=entered)
                    if call is None
                    else await call.invoke(request=request, context=entered)
                )
                return _validated_result(selected.job, raw)
        finally:
            if call is not None:
                await _notify_use_case_observers(self.use_case_observers, call)


def create_job_dispatcher(
    *,
    jobs: JobGroup,
    use_case_observers: Sequence[UseCaseObserver] = (),
) -> JobDispatcher:
    """Compose registered job handlers into a validated dispatcher."""
    raw_jobs = cast(object, jobs)
    if not isinstance(raw_jobs, JobGroup):
        raise ConfigurationError(
            f"create_job_dispatcher: jobs must be a JobGroup, got {type(jobs).__name__}"
        )
    try:
        observers = _validated_observers(use_case_observers)
    except Exception as exc:
        raise ConfigurationError(f"create_job_dispatcher: {exc}") from exc
    return JobDispatcher(jobs=raw_jobs, use_case_observers=observers)


def _handler_signature(
    name: str,
    use_case: Callable[..., Awaitable[Any]],
) -> inspect.Signature:
    if not inspect.iscoroutinefunction(use_case):
        raise JobBindingError(
            f"job_handler({name!r}): use case {_describe(use_case)} must be "
            "an async function"
        )
    try:
        return inspect.signature(use_case)
    except (TypeError, ValueError) as exc:
        raise JobBindingError(
            f"job_handler({name!r}): could not inspect use case "
            f"{_describe(use_case)}: {exc}"
        ) from exc


def _validate_handler_shape(
    name: str,
    use_case: Callable[..., Awaitable[Any]],
    signature: inspect.Signature,
) -> None:
    parameters = signature.parameters
    for argument in ("request", "context"):
        parameter = parameters.get(argument)
        if parameter is None:
            raise JobBindingError(
                f"job_handler({name!r}): use case {_describe(use_case)} must "
                f"accept a {argument!r} argument"
            )
        if parameter.kind not in (
            inspect.Parameter.POSITIONAL_OR_KEYWORD,
            inspect.Parameter.KEYWORD_ONLY,
        ):
            raise JobBindingError(
                f"job_handler({name!r}): use case {_describe(use_case)} "
                f"parameter {argument!r} must be addressable by keyword"
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
            raise JobBindingError(
                f"job_handler({name!r}): use case {_describe(use_case)} has "
                f"required parameter {parameter.name!r}; jobs only pass "
                "'request' and 'context'"
            )


def _resolved_annotation(
    name: str,
    use_case: Callable[..., Awaitable[Any]],
    annotation: Any,
    *,
    label: str,
) -> Any:
    if annotation is inspect.Signature.empty:
        raise JobBindingError(
            f"job_handler({name!r}): use case {_describe(use_case)} {label} "
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
            raise JobBindingError(
                f"job_handler({name!r}): could not resolve {label} annotation "
                f"{original!r}: cyclic forward reference"
            )
        seen.add(annotation)
        try:
            annotation = eval(annotation, namespace)
        except Exception as exc:
            raise JobBindingError(
                f"job_handler({name!r}): could not resolve {label} annotation "
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
        raise JobBindingError(
            f"job({name!r}): Pydantic cannot use {label} annotation "
            f"{_type_name(annotation)}: {exc}"
        ) from exc


def _request_schema(
    name: str,
    adapter: TypeAdapter[Any],
) -> tuple[dict[str, Any], Validator]:
    try:
        schema = cast(
            dict[str, Any],
            json.loads(
                json.dumps(
                    adapter.json_schema(mode="validation", by_alias=True),
                    allow_nan=False,
                    ensure_ascii=False,
                    separators=(",", ":"),
                    sort_keys=True,
                )
            ),
        )
        Draft202012Validator.check_schema(schema)
        validator = Draft202012Validator(
            schema,
            format_checker=FormatChecker(),
        )
    except Exception as exc:
        raise JobBindingError(
            f"job({name!r}): request JSON Schema is invalid: {exc}"
        ) from exc
    return schema, validator


def _validated_result(declaration: Job[Any, Any], raw: Any) -> Any:
    try:
        adapter = declaration._result_adapter  # pyright: ignore[reportPrivateUsage]
        validated = adapter.validate_python(raw)
        json_value = adapter.dump_python(
            validated,
            mode="json",
            by_alias=True,
            warnings="error",
        )
        json.dumps(json_value, allow_nan=False)
    except (CancelledError, KeyboardInterrupt):
        raise
    except Exception as exc:
        raise JobResultError(
            f"job {declaration.name!r} returned a value that does not match "
            f"{_type_name(declaration.result)}"
        ) from exc
    return validated


def _append_handlers(
    flattened: list[JobHandler[Any, Any]],
    item: object,
    *,
    index: int,
) -> None:
    if isinstance(item, JobHandler):
        flattened.append(cast(JobHandler[Any, Any], item))
        return
    if isinstance(item, JobGroup):
        flattened.extend(item.handlers)
        return
    if isinstance(item, Sequence) and not isinstance(item, str | bytes):
        sequence = cast(Sequence[object], item)
        for nested_index, nested in enumerate(sequence):
            if not isinstance(nested, JobHandler):
                raise ConfigurationError(
                    f"job_group item {index}[{nested_index}] must be a "
                    f"JobHandler, got {type(nested).__name__}"
                )
            flattened.append(cast(JobHandler[Any, Any], nested))
        return
    raise ConfigurationError(
        f"job_group item {index} must be a JobHandler, JobGroup, or sequence "
        f"of JobHandler values, got {type(item).__name__}"
    )


def _require_job(value: object, *, label: str) -> None:
    if not isinstance(value, Job):
        raise ConfigurationError(
            f"{label}: declaration must be a Job, got {type(value).__name__}"
        )


def _describe(value: object) -> str:
    name = getattr(value, "__qualname__", None) or repr(value)
    module = getattr(value, "__module__", None)
    return f"{module}.{name}" if module else str(name)


def _type_name(value: object) -> str:
    return getattr(value, "__name__", repr(value))


__all__ = [
    "JOB_MANIFEST_VERSION",
    "Job",
    "JobBindingError",
    "JobDispatcher",
    "JobGroup",
    "JobHandler",
    "JobManifest",
    "JobManifestEntry",
    "JobMessage",
    "JobNotFoundError",
    "JobResultError",
    "create_job_dispatcher",
    "job",
    "job_group",
    "job_handler",
    "job_manifest",
    "job_message",
]
