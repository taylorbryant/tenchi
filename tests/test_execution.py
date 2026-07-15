"""execute() gives non-HTTP entrypoints the server's boundary guarantees."""

from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from dataclasses import dataclass

import pytest
from pydantic import BaseModel, ValidationError

from tenchi.errors import AppError, ErrorDef
from tenchi.execution import ExecutionError, execute, open_context


class Note(BaseModel):
    title: str


@dataclass(frozen=True, slots=True)
class Context:
    log: list[str]


boom = ErrorDef(code="BOOM", status=409, message="Boom")


async def create_note(request: Note, context: Context) -> Note:
    context.log.append(f"created {request.title}")
    return request


async def ping(context: Context) -> str:
    return "pong"


async def fail(context: Context) -> str:
    raise AppError(boom)


async def test_python_input_is_validated_against_the_annotation() -> None:
    log: list[str] = []

    note = await execute(create_note, request={"title": "x"}, context=Context(log))

    assert note == Note(title="x")
    assert log == ["created x"]


async def test_json_input_is_validated_the_same_way() -> None:
    note = await execute(
        create_note, request_json=b'{"title": "x"}', context=Context([])
    )

    assert note == Note(title="x")


async def test_invalid_input_raises_before_any_work_runs() -> None:
    log: list[str] = []

    with pytest.raises(ValidationError):
        await execute(create_note, request={"title": 42}, context=Context(log))

    assert log == []


async def test_invalid_input_never_opens_the_context() -> None:
    events: list[str] = []

    @asynccontextmanager
    async def unit_of_work() -> AsyncGenerator[Context]:
        events.append("enter")
        yield Context(events)
        events.append("commit")

    with pytest.raises(ValidationError):
        await execute(create_note, request={}, context=unit_of_work)

    assert events == []


async def test_context_factory_and_scope_semantics_match_the_server() -> None:
    events: list[str] = []

    @asynccontextmanager
    async def unit_of_work() -> AsyncGenerator[Context]:
        events.append("enter")
        try:
            yield Context(events)
            events.append("commit")
        except BaseException:
            events.append("rollback")
            raise

    await execute(create_note, request={"title": "x"}, context=unit_of_work)
    assert events == ["enter", "created x", "commit"]

    events.clear()
    with pytest.raises(AppError):
        await execute(fail, context=unit_of_work)
    assert events == ["enter", "rollback"]


async def test_plain_context_value_passes_through() -> None:
    assert await execute(ping, context=Context([])) == "pong"


async def test_async_context_factory_is_awaited() -> None:
    async def make_context() -> Context:
        return Context([])

    assert await execute(ping, context=make_context) == "pong"


async def test_missing_request_is_rejected() -> None:
    with pytest.raises(ExecutionError, match="pass request= or request_json="):
        await execute(create_note, context=Context([]))


async def test_undeclared_request_is_rejected_not_dropped() -> None:
    with pytest.raises(ExecutionError, match="the input would be dropped"):
        await execute(ping, request={"title": "x"}, context=Context([]))


async def test_both_input_forms_together_are_rejected() -> None:
    with pytest.raises(ExecutionError, match="not both"):
        await execute(
            create_note,
            request={"title": "x"},
            request_json=b"{}",
            context=Context([]),
        )


async def test_unannotated_request_parameter_is_rejected() -> None:
    async def sloppy(request, context: Context) -> None:  # type: ignore[no-untyped-def]
        return None

    with pytest.raises(ExecutionError, match="must be annotated"):
        await execute(
            sloppy,  # pyright: ignore[reportUnknownArgumentType]
            request={},
            context=Context([]),
        )


async def test_use_case_without_context_parameter_is_rejected() -> None:
    async def rogue() -> None:
        return None

    with pytest.raises(ExecutionError, match="must accept a 'context'"):
        await execute(rogue, context=Context([]))


async def test_type_checking_only_context_annotation_still_executes() -> None:
    # The context annotation resolves nowhere at runtime — the idiom the
    # layering rules encourage. Only the request annotation is resolved.
    source = (
        "from __future__ import annotations\n"
        "from typing import TYPE_CHECKING\n"
        "if TYPE_CHECKING:\n"
        "    from nowhere import Ghost\n"
        "async def uc(request: int, context: Ghost) -> int:\n"
        "    return request + 1\n"
    )
    namespace: dict[str, object] = {}
    exec(source, namespace)

    assert await execute(namespace["uc"], request=41, context=object()) == 42  # type: ignore[arg-type]


async def test_redundantly_quoted_future_request_annotation_executes() -> None:
    source = (
        "from __future__ import annotations\n"
        "async def uc(request: 'int', context: object) -> int:\n"
        "    return request + 1\n"
    )
    namespace: dict[str, object] = {}
    exec(source, namespace)

    assert await execute(namespace["uc"], request=41, context=object()) == 42  # type: ignore[arg-type]


async def test_unresolvable_request_annotation_is_a_framed_error() -> None:
    source = (
        "from __future__ import annotations\n"
        "async def uc(request: Ghost, context: object) -> None:\n"
        "    return None\n"
    )
    namespace: dict[str, object] = {}
    exec(source, namespace)

    with pytest.raises(ExecutionError, match="could not resolve the 'request'"):
        await execute(namespace["uc"], request=1, context=object())  # type: ignore[arg-type]


async def test_partial_use_cases_work() -> None:
    import functools

    async def flavored(request: Note, context: Context, flavor: str) -> str:
        return f"{request.title}:{flavor}"

    bound = functools.partial(flavored, flavor="mint")

    result = await execute(bound, request={"title": "x"}, context=Context([]))
    assert result == "x:mint"


async def test_kwargs_use_cases_are_accepted_without_request_input() -> None:
    async def flexible(**kwargs: object) -> str:
        assert isinstance(kwargs["context"], Context)
        return "ok"

    assert await execute(flexible, context=Context([])) == "ok"


async def test_kwargs_use_cases_cannot_take_unvalidatable_request() -> None:
    async def flexible(**kwargs: object) -> None:
        return None

    with pytest.raises(ExecutionError, match="cannot be validated"):
        await execute(flexible, request={"title": "x"}, context=Context([]))


async def test_signature_problems_are_caught_before_the_context_opens() -> None:
    events: list[str] = []

    @asynccontextmanager
    async def unit_of_work() -> AsyncGenerator[Context]:
        events.append("enter")
        yield Context(events)
        events.append("commit")

    async def positional(request: Note, context: Context, /) -> None:
        return None

    async def needy(request: Note, context: Context, repo: object) -> None:
        return None

    with pytest.raises(ExecutionError, match="addressable by keyword"):
        await execute(positional, request={"title": "x"}, context=unit_of_work)
    with pytest.raises(ExecutionError, match="required parameter 'repo'"):
        await execute(needy, request={"title": "x"}, context=unit_of_work)

    assert events == []  # a miswired call never starts a unit of work


async def test_sync_use_case_is_rejected_before_the_context_opens() -> None:
    events: list[str] = []

    @asynccontextmanager
    async def unit_of_work() -> AsyncGenerator[Context]:
        events.append("enter")
        yield Context(events)

    def sync_use_case(context: Context) -> str:
        return "not awaitable"

    with pytest.raises(ExecutionError, match="must be an async function"):
        await execute(sync_use_case, context=unit_of_work)  # type: ignore[arg-type]

    assert events == []


async def test_context_factory_arity_is_rejected_before_calling_it() -> None:
    called = False

    def needs_state(state: str) -> Context:
        nonlocal called
        called = True
        return Context([state])

    with pytest.raises(ExecutionError, match="must accept zero arguments"):
        await execute(ping, context=needs_state)

    assert not called


async def test_context_classes_are_rejected_as_ambiguous_factories() -> None:
    with pytest.raises(ExecutionError, match="pass a context instance, not a class"):
        await execute(ping, context=Context)


async def test_context_factory_exception_is_not_misclassified() -> None:
    def broken_factory() -> Context:
        raise TypeError("factory bug")

    with pytest.raises(TypeError, match="factory bug") as raised:
        await execute(ping, context=broken_factory)

    assert type(raised.value) is TypeError


async def test_defaulted_request_parameter_uses_its_default() -> None:
    async def optional_input(context: Context, request: Note | None = None) -> str:
        return request.title if request else "default"

    assert await execute(optional_input, context=Context([])) == "default"
    assert (
        await execute(optional_input, request={"title": "x"}, context=Context([]))
        == "x"
    )


async def test_sync_context_managers_are_rejected() -> None:
    from contextlib import contextmanager

    @contextmanager
    def sync_scope():
        yield Context([])

    with pytest.raises(ExecutionError, match="must be async"):
        await execute(ping, context=sync_scope)


async def test_bare_async_generators_are_rejected() -> None:
    async def forgot_decorator() -> AsyncGenerator[Context]:
        yield Context([])

    with pytest.raises(ExecutionError, match="@asynccontextmanager"):
        await execute(ping, context=forgot_decorator)


async def test_unhashable_annotations_skip_the_cache() -> None:
    source = (
        "from typing import Annotated\n"
        "async def uc(request: Annotated[int, {'note': 'unhashable'}], "
        "context: object) -> int:\n"
        "    return request\n"
    )
    namespace: dict[str, object] = {}
    exec(source, namespace)

    assert await execute(namespace["uc"], request=7, context=object()) == 7  # type: ignore[arg-type]


async def test_open_context_is_usable_directly() -> None:
    events: list[str] = []

    @asynccontextmanager
    async def unit_of_work() -> AsyncGenerator[Context]:
        events.append("enter")
        yield Context(events)
        events.append("exit")

    async with open_context(unit_of_work) as context:
        context.log.append("inside")

    assert events == ["enter", "inside", "exit"]
