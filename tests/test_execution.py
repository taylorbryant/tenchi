"""execute() gives non-HTTP entrypoints the server's boundary guarantees."""

from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from dataclasses import dataclass

import pytest
from pydantic import BaseModel, ValidationError

from tenchi.errors import AppError, ErrorDef
from tenchi.execution import execute, open_context


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
    with pytest.raises(TypeError, match="pass request= or request_json="):
        await execute(create_note, context=Context([]))


async def test_undeclared_request_is_rejected_not_dropped() -> None:
    with pytest.raises(TypeError, match="does not declare a 'request'"):
        await execute(ping, request={"title": "x"}, context=Context([]))


async def test_both_input_forms_together_are_rejected() -> None:
    with pytest.raises(TypeError, match="not both"):
        await execute(
            create_note,
            request={"title": "x"},
            request_json=b"{}",
            context=Context([]),
        )


async def test_unannotated_request_parameter_is_rejected() -> None:
    async def sloppy(request, context: Context) -> None:  # type: ignore[no-untyped-def]
        return None

    with pytest.raises(TypeError, match="must be annotated"):
        await execute(
            sloppy,  # pyright: ignore[reportUnknownArgumentType]
            request={},
            context=Context([]),
        )


async def test_use_case_without_context_parameter_is_rejected() -> None:
    async def rogue() -> None:
        return None

    with pytest.raises(TypeError, match="must accept a 'context'"):
        await execute(rogue, context=Context([]))


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
