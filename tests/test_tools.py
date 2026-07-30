"""Typed application tools stay transport-neutral and fail closed."""

# pyright: strict, reportUnnecessaryTypeIgnoreComment=true

import asyncio
import json
import os
import subprocess
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Annotated, Any, TypeAlias, cast

import pytest
from pydantic import (
    BaseModel,
    Field,
    PlainSerializer,
    TypeAdapter,
    ValidationError,
    field_validator,
)
from pydantic_core import core_schema

from tenchi._schema_compatibility import ChangeSeverity, compare_schema
from tenchi.contracts import contract
from tenchi.errors import AppError, ConfigurationError, ErrorDef
from tenchi.execution import UseCaseOutcome
from tenchi.routes import route
from tenchi.tools import (
    TOOL_MANIFEST_VERSION,
    Tool,
    ToolBinding,
    ToolBindingError,
    ToolGroup,
    ToolInvocationError,
    ToolManifest,
    ToolNotFoundError,
    ToolResultError,
    ToolRunner,
    create_tool_runner,
    tool,
    tool_group,
    tool_handler,
    tool_manifest,
)

TOOL_MANIFEST_SNAPSHOT_PATH = Path(__file__).with_name(
    f"tool_manifest_protocol_v{TOOL_MANIFEST_VERSION}_snapshot.json"
)
UPDATE_TOOL_MANIFEST_SNAPSHOT = "TENCHI_UPDATE_TOOL_MANIFEST_SNAPSHOT"
TOOL_MANIFEST_BASE_REF = "TENCHI_AGENT_PROTOCOL_BASE_REF"
REPOSITORY_ROOT = Path(__file__).parent.parent
_request_schema_calls = 0


def serialize_as_string(value: int) -> str:
    return str(value)


MisserializedInt: TypeAlias = Annotated[  # noqa: UP040
    int,
    PlainSerializer(lambda value: f"wrong:{value}"),
]
StringSerializedInt: TypeAlias = Annotated[  # noqa: UP040
    int,
    PlainSerializer(serialize_as_string, return_type=str),
]


class SearchInput(BaseModel):
    query: str = Field(min_length=1)
    limit: int = Field(default=10, ge=1, le=100)


class SearchResult(BaseModel):
    matches: list[str]


class ExplodingInput(BaseModel):
    value: str

    @field_validator("value")
    @classmethod
    def reject_with_secret(cls, value: str) -> str:
        raise ToolResultError(f"validator secret: {value}")


class InvalidRequestSchema:
    @classmethod
    def __get_pydantic_core_schema__(
        cls,
        source_type: Any,
        handler: Any,
    ) -> core_schema.CoreSchema:
        del cls, source_type, handler
        return core_schema.str_schema()

    @classmethod
    def __get_pydantic_json_schema__(
        cls,
        schema: core_schema.CoreSchema,
        handler: Any,
    ) -> dict[str, Any]:
        del cls, schema, handler
        return {"type": "not-a-json-schema-type"}


class NonJsonRequestSchema:
    @classmethod
    def __get_pydantic_core_schema__(
        cls,
        source_type: Any,
        handler: Any,
    ) -> core_schema.CoreSchema:
        del cls, source_type, handler
        return core_schema.str_schema()

    @classmethod
    def __get_pydantic_json_schema__(
        cls,
        schema: core_schema.CoreSchema,
        handler: Any,
    ) -> dict[str, Any]:
        del cls, schema, handler
        return {
            "type": "string",
            "default": object(),
        }


class ChangingRequestSchema:
    @classmethod
    def __get_pydantic_core_schema__(
        cls,
        source_type: Any,
        handler: Any,
    ) -> core_schema.CoreSchema:
        del cls, source_type, handler
        return core_schema.str_schema()

    @classmethod
    def __get_pydantic_json_schema__(
        cls,
        schema: core_schema.CoreSchema,
        handler: Any,
    ) -> dict[str, Any]:
        del cls, schema, handler
        global _request_schema_calls
        _request_schema_calls += 1
        return {
            "type": "string",
            "title": f"render-{_request_schema_calls}",
        }


@dataclass(frozen=True, slots=True)
class Context:
    events: list[str]


async def search(request: SearchInput, context: Context) -> SearchResult:
    context.events.append(f"search:{request.query}:{request.limit}")
    return SearchResult(matches=[request.query])


async def ping(context: Context) -> str:
    context.events.append("ping")
    return "pong"


async def echo_changing_schema(
    request: ChangingRequestSchema,
    context: Context,
) -> str:
    del context
    return str(request)


def search_tool(
    *,
    errors: tuple[ErrorDef, ...] = (),
) -> Tool[SearchInput, SearchResult]:
    return tool(
        "projects.search",
        request=SearchInput,
        result=SearchResult,
        description="  Search visible projects.  ",
        errors=errors,
        read_only=True,
        open_world=False,
    )


def test_tool_declaration_builds_validators_and_conservative_annotations() -> None:
    readable = search_tool()
    writable = tool(
        "projects.create",
        request=SearchInput,
        result=SearchResult,
        description="Create a project.",
    )

    assert readable.description == "Search visible projects."
    assert readable.read_only
    assert not readable.destructive
    assert readable.idempotent
    assert not readable.open_world
    assert writable.destructive
    assert not writable.idempotent
    assert writable.open_world


@pytest.mark.parametrize(
    "name",
    ["Bad", "bad-name", ".bad", "bad.", "bad..name", "two words"],
)
def test_tool_names_are_stable_dotted_snake_case(name: str) -> None:
    with pytest.raises(ToolBindingError, match="dotted snake_case"):
        tool(name, result=str, description="Invalid.")


def test_tool_declaration_validates_description_and_annotations() -> None:
    with pytest.raises(ToolBindingError, match="non-empty"):
        tool("empty", result=str, description=" ")
    with pytest.raises(ToolBindingError, match="read_only must be a bool"):
        tool(
            "wrong.read_only",
            result=str,
            description="Wrong.",
            read_only=cast(Any, 1),
        )
    with pytest.raises(ToolBindingError, match="cannot be destructive"):
        tool(
            "wrong.destructive",
            result=str,
            description="Wrong.",
            read_only=True,
            destructive=True,
        )
    with pytest.raises(ToolBindingError, match="must be idempotent"):
        tool(
            "wrong.idempotent",
            result=str,
            description="Wrong.",
            read_only=True,
            idempotent=False,
        )


def test_tool_declaration_validates_types_and_declared_errors() -> None:
    unavailable = ErrorDef(code="UNAVAILABLE", status=503, message="Unavailable")
    declared = search_tool(errors=(unavailable, unavailable))

    assert declared.errors == (unavailable,)
    assert declared.declares_error(unavailable)

    with pytest.raises(ToolBindingError, match="Pydantic cannot use"):
        tool(
            "invalid.type",
            request=cast(Any, object()),
            result=str,
            description="Invalid.",
        )
    with pytest.raises(ToolBindingError, match="not-a-json-schema-type"):
        tool(
            "invalid.request_schema",
            request=InvalidRequestSchema,
            result=str,
            description="Invalid.",
        )
    with pytest.raises(ToolBindingError, match="not JSON serializable"):
        tool(
            "invalid.non_json_request_schema",
            request=NonJsonRequestSchema,
            result=str,
            description="Invalid.",
        )
    with pytest.raises(ConfigurationError, match=r"errors\[0\].*ErrorDef"):
        tool(
            "invalid.error",
            result=str,
            description="Invalid.",
            errors=cast(Any, ("no",)),
        )


def test_tool_manifest_retains_the_validated_request_schema() -> None:
    global _request_schema_calls
    _request_schema_calls = 0
    declaration = tool(
        "system.changing_schema",
        request=ChangingRequestSchema,
        result=str,
        description="Retain one request schema.",
        read_only=True,
    )
    tools = tool_group(tool_handler(declaration, echo_changing_schema))

    first = tool_manifest(tools)
    second = tool_manifest(tools)

    assert first == second
    assert first["tools"][0]["input_schema"]["title"] == "render-1"
    assert _request_schema_calls == 1


def test_tool_manifest_does_not_expose_retained_schemas_to_mutation() -> None:
    tools = tool_group(tool_handler(search_tool(), search))
    first = tool_manifest(tools)
    entry = first["tools"][0]

    entry["input_schema"].clear()
    entry["output_schema"].clear()

    second = tool_manifest(tools)

    assert second["tools"][0]["input_schema"]["type"] == "object"
    assert second["tools"][0]["output_schema"]["type"] == "object"


def test_tool_handler_accepts_the_same_use_case_as_an_http_route() -> None:
    declaration = search_tool()
    binding = tool_handler(declaration, search)
    http = route(
        contract(
            method="POST",
            path="/projects/search",
            request=SearchInput,
            response=SearchResult,
        ),
        search,
    )

    assert binding.tool is declaration
    assert binding.use_case is search
    assert http.use_case is search


def test_tool_handler_rejects_invalid_use_cases_at_composition() -> None:
    declaration = search_tool()

    def sync(request: SearchInput, context: Context) -> SearchResult:
        return SearchResult(matches=[])

    async def missing_request(context: Context) -> SearchResult:
        return SearchResult(matches=[])

    async def missing_context(request: SearchInput) -> SearchResult:
        return SearchResult(matches=[])

    async def wrong_request(request: int, context: Context) -> SearchResult:
        return SearchResult(matches=[])

    async def wrong_result(request: SearchInput, context: Context) -> str:
        return "wrong"

    async def extra(
        request: SearchInput,
        context: Context,
        optional: str = "extra",
    ) -> SearchResult:
        return SearchResult(matches=[])

    with pytest.raises(ToolBindingError, match="async function"):
        tool_handler(declaration, sync)  # type: ignore[arg-type]
    with pytest.raises(ToolBindingError, match="accept a 'request'"):
        tool_handler(declaration, missing_request)
    with pytest.raises(ToolBindingError, match="accept a 'context'"):
        tool_handler(declaration, missing_context)
    with pytest.raises(ToolBindingError, match="request annotation int"):
        tool_handler(declaration, wrong_request)
    with pytest.raises(ToolBindingError, match="return annotation str"):
        tool_handler(
            declaration,
            wrong_result,  # pyright: ignore[reportArgumentType]
        )
    with pytest.raises(ToolBindingError, match="unsupported parameter"):
        tool_handler(declaration, extra)


def test_no_input_tools_reject_handlers_with_request_parameters() -> None:
    declaration = tool(
        "system.ping",
        result=str,
        description="Confirm the application is responsive.",
        read_only=True,
        open_world=False,
    )

    async def unnecessary(request: None, context: Context) -> str:
        return "pong"

    assert tool_handler(declaration, ping).use_case is ping
    with pytest.raises(ToolBindingError, match="declares no request"):
        tool_handler(declaration, unnecessary)


def test_tool_groups_flatten_and_reject_duplicate_names() -> None:
    first = tool_handler(search_tool(), search)
    second = tool_handler(
        tool(
            "system.ping",
            result=str,
            description="Ping.",
            read_only=True,
        ),
        ping,
    )

    assert tool_group(first, tool_group(second)).handlers == (first, second)
    with pytest.raises(ConfigurationError, match="duplicate tool name"):
        tool_group(first, first)
    with pytest.raises(ConfigurationError, match="must be a ToolBinding"):
        tool_group(cast(Any, "wrong"))


def test_direct_tool_bindings_enforce_handler_validation() -> None:
    declaration = search_tool()

    def sync(request: SearchInput, context: Context) -> SearchResult:
        return SearchResult(matches=[])

    async def wrong_result(request: SearchInput, context: Context) -> str:
        return "wrong"

    with pytest.raises(ToolBindingError, match="async function"):
        ToolBinding(
            tool=declaration,
            use_case=sync,  # type: ignore[arg-type]
        )
    with pytest.raises(ToolBindingError, match="return annotation str"):
        ToolBinding(
            tool=declaration,
            use_case=wrong_result,  # pyright: ignore[reportArgumentType]
        )


def test_direct_tool_groups_enforce_contents_and_unique_names() -> None:
    binding = tool_handler(search_tool(), search)

    assert ToolGroup(handlers=(binding,)).handlers == (binding,)
    with pytest.raises(ConfigurationError, match="handlers must be a tuple"):
        ToolGroup(handlers=cast(Any, [binding]))
    with pytest.raises(ConfigurationError, match=r"handlers\[0\].*ToolBinding"):
        ToolGroup(handlers=cast(Any, ("wrong",)))
    with pytest.raises(ConfigurationError, match="duplicate tool name"):
        ToolGroup(handlers=(binding, binding))


async def test_runner_validates_input_before_opening_resources() -> None:
    events: list[str] = []

    @asynccontextmanager
    async def lifespan() -> AsyncGenerator[list[str]]:
        events.append("lifespan:enter")
        yield events
        events.append("lifespan:exit")

    def context_factory(state: list[str]) -> Context:
        state.append("context:create")
        return Context(state)

    runner = create_tool_runner(
        tools=tool_group(tool_handler(search_tool(), search)),
        context_factory=context_factory,
        lifespan=lifespan,
    )

    with pytest.raises(ValidationError):
        await runner.call("projects.search", input={"query": ""})

    assert events == []


async def test_runner_masks_unexpected_input_validator_failures() -> None:
    events: list[str] = []

    async def handle(request: ExplodingInput, context: Context) -> str:
        return request.value

    runner = create_tool_runner(
        tools=tool_group(
            tool_handler(
                tool(
                    "system.explode",
                    request=ExplodingInput,
                    result=str,
                    description="Exercise a custom validator.",
                ),
                handle,
            )
        ),
        context_factory=lambda: events.append("opened") or Context(events),
    )

    with pytest.raises(
        ToolInvocationError,
        match=r"tool 'system\.explode' failed",
    ) as failure:
        await runner.call("system.explode", input={"value": "password"})

    assert isinstance(failure.value.__cause__, ToolResultError)
    assert "password" not in str(failure.value)
    assert events == []


async def test_runner_owns_lifespan_context_and_validates_result_before_commit() -> (
    None
):
    events: list[str] = []

    @asynccontextmanager
    async def lifespan() -> AsyncGenerator[list[str]]:
        events.append("lifespan:enter")
        try:
            yield events
        finally:
            events.append("lifespan:exit")

    @asynccontextmanager
    async def context_factory(state: list[str]) -> AsyncGenerator[Context]:
        state.append("context:enter")
        try:
            yield Context(state)
            state.append("context:commit")
        except BaseException:
            state.append("context:rollback")
            raise

    runner = create_tool_runner(
        tools=tool_group(tool_handler(search_tool(), search)),
        context_factory=context_factory,
        lifespan=lifespan,
    )

    result = await runner.call(
        "projects.search",
        input_json='{"query": "tenchi", "limit": 2}',
    )

    assert result == SearchResult(matches=["tenchi"])
    assert events == [
        "lifespan:enter",
        "context:enter",
        "search:tenchi:2",
        "context:commit",
        "lifespan:exit",
    ]


async def test_invalid_result_rolls_back_the_context() -> None:
    events: list[str] = []

    async def wrong(request: SearchInput, context: Context) -> SearchResult:
        context.events.append("run")
        return "wrong"  # type: ignore[return-value]

    @asynccontextmanager
    async def context_factory() -> AsyncGenerator[Context]:
        events.append("context:enter")
        try:
            yield Context(events)
            events.append("context:commit")
        except BaseException:
            events.append("context:rollback")
            raise

    runner = create_tool_runner(
        tools=tool_group(tool_handler(search_tool(), wrong)),
        context_factory=context_factory,
    )

    with pytest.raises(ToolResultError, match=r"tool 'projects\.search' returned"):
        await runner.call("projects.search", input={"query": "tenchi"})

    assert events == ["context:enter", "run", "context:rollback"]


async def test_serialized_result_must_match_the_manifest_schema() -> None:
    events: list[str] = []

    async def misserialize(context: Context) -> MisserializedInt:
        context.events.append("run")
        return 1

    @asynccontextmanager
    async def context_factory() -> AsyncGenerator[Context]:
        events.append("context:enter")
        try:
            yield Context(events)
            events.append("context:commit")
        except BaseException:
            events.append("context:rollback")
            raise

    declaration = tool(
        "system.misserialize",
        result=MisserializedInt,
        description="Return a badly serialized value.",
        read_only=True,
    )
    runner = create_tool_runner(
        tools=tool_group(tool_handler(declaration, misserialize)),
        context_factory=context_factory,
    )

    assert tool_manifest(runner.tools)["tools"][0]["output_schema"] == {
        "type": "integer"
    }
    with pytest.raises(ToolResultError, match="does not match"):
        await runner.call("system.misserialize")

    assert events == ["context:enter", "run", "context:rollback"]


async def test_schema_declared_one_way_serializers_remain_supported() -> None:
    async def serialize(context: Context) -> StringSerializedInt:
        return 1

    declaration = tool(
        "system.serialize",
        result=StringSerializedInt,
        description="Return a deliberately serialized value.",
        read_only=True,
    )
    runner = create_tool_runner(
        tools=tool_group(tool_handler(declaration, serialize)),
        context_factory=lambda: Context([]),
    )

    assert tool_manifest(runner.tools)["tools"][0]["output_schema"] == {
        "type": "string"
    }
    assert await runner.call("system.serialize") == 1


async def test_declared_errors_cross_the_boundary_and_other_failures_are_masked() -> (
    None
):
    visible = ErrorDef(code="VISIBLE", status=409, message="Visible")
    hidden = ErrorDef(code="HIDDEN", status=409, message="Hidden")

    async def reject_visible(
        request: SearchInput,
        context: Context,
    ) -> SearchResult:
        raise AppError(visible, details={"safe": True})

    async def reject_hidden(
        request: SearchInput,
        context: Context,
    ) -> SearchResult:
        raise AppError(hidden, details={"secret": "do not expose"})

    async def explode(request: SearchInput, context: Context) -> SearchResult:
        raise RuntimeError("database password appeared here")

    async def spoof_result_error(
        request: SearchInput,
        context: Context,
    ) -> SearchResult:
        raise ToolResultError("result secret appeared here")

    bindings = (
        tool_handler(search_tool(errors=(visible,)), reject_visible),
        tool_handler(
            tool(
                "projects.hidden",
                request=SearchInput,
                result=SearchResult,
                description="Reject unexpectedly.",
            ),
            reject_hidden,
        ),
        tool_handler(
            tool(
                "projects.explode",
                request=SearchInput,
                result=SearchResult,
                description="Fail unexpectedly.",
            ),
            explode,
        ),
        tool_handler(
            tool(
                "projects.spoof_result",
                request=SearchInput,
                result=SearchResult,
                description="Spoof a framework result error.",
            ),
            spoof_result_error,
        ),
    )
    runner = create_tool_runner(
        tools=tool_group(bindings),
        context_factory=lambda: Context([]),
    )

    with pytest.raises(AppError) as declared:
        await runner.call("projects.search", input={"query": "x"})
    assert declared.value.code == "VISIBLE"
    assert declared.value.details == {"safe": True}

    with pytest.raises(
        ToolInvocationError,
        match=r"tool 'projects\.hidden' failed",
    ) as (undeclared):
        await runner.call("projects.hidden", input={"query": "x"})
    assert isinstance(undeclared.value.__cause__, AppError)
    assert "secret" not in str(undeclared.value)

    with pytest.raises(
        ToolInvocationError,
        match=r"tool 'projects\.explode' failed",
    ) as (unexpected):
        await runner.call("projects.explode", input={"query": "x"})
    assert isinstance(unexpected.value.__cause__, RuntimeError)
    assert "password" not in str(unexpected.value)

    with pytest.raises(
        ToolInvocationError,
        match=r"tool 'projects\.spoof_result' failed",
    ) as spoofed:
        await runner.call("projects.spoof_result", input={"query": "x"})
    assert isinstance(spoofed.value.__cause__, ToolResultError)
    assert "secret" not in str(spoofed.value)


async def test_runner_reports_input_misuse_and_unknown_tools() -> None:
    runner = create_tool_runner(
        tools=tool_group(
            tool_handler(search_tool(), search),
            tool_handler(
                tool(
                    "system.ping",
                    result=str,
                    description="Ping.",
                    read_only=True,
                ),
                ping,
            ),
        ),
        context_factory=lambda: Context([]),
    )

    with pytest.raises(ToolNotFoundError, match="unknown tool 'missing'"):
        await runner.call("missing")
    with pytest.raises(ToolBindingError, match="requires input"):
        await runner.call("projects.search")
    with pytest.raises(ToolBindingError, match="not both"):
        await runner.call(
            "projects.search",
            input={"query": "x"},
            input_json='{"query": "x"}',
        )
    assert await runner.call("system.ping", input={}) == "pong"
    assert await runner.call("system.ping", input_json="{}") == "pong"
    with pytest.raises(ValidationError):
        await runner.call("system.ping", input=None)
    with pytest.raises(ValidationError):
        await runner.call("system.ping", input={"unexpected": True})
    with pytest.raises(ValidationError):
        await runner.call("system.ping", input_json='{"unexpected": true}')
    assert await runner.call("system.ping") == "pong"


def test_runner_validates_composition_eagerly() -> None:
    async def lifespan_with_argument(value: object) -> AsyncGenerator[object]:
        yield value

    def stateful_context(state: object) -> object:
        return state

    with pytest.raises(ConfigurationError, match="no lifespan"):
        create_tool_runner(
            tools=tool_group(),
            context_factory=stateful_context,
        )
    with pytest.raises(ConfigurationError, match="no lifespan"):
        ToolRunner(
            tools=tool_group(),
            context_factory=stateful_context,
        )
    with pytest.raises(ConfigurationError, match=r"lifespan.*zero arguments"):
        create_tool_runner(
            tools=tool_group(),
            context_factory=lambda: object(),
            lifespan=lifespan_with_argument,  # type: ignore[arg-type]
        )


async def test_direct_tool_runner_construction_preserves_lifecycle_wiring() -> None:
    events: list[str] = []

    @asynccontextmanager
    async def lifespan() -> AsyncGenerator[list[str]]:
        events.append("lifespan:enter")
        yield events
        events.append("lifespan:exit")

    def context_factory(state: list[str]) -> Context:
        state.append("context:create")
        return Context(state)

    runner = ToolRunner(
        tools=tool_group(
            tool_handler(
                tool(
                    "system.ping",
                    result=str,
                    description="Ping.",
                    read_only=True,
                ),
                ping,
            )
        ),
        context_factory=context_factory,
        lifespan=lifespan,
    )

    assert await runner.call("system.ping") == "pong"
    assert events == [
        "lifespan:enter",
        "context:create",
        "ping",
        "lifespan:exit",
    ]


async def test_tool_calls_share_use_case_observability_after_cleanup() -> None:
    events: list[str] = []
    outcomes: list[UseCaseOutcome] = []

    @asynccontextmanager
    async def context_factory() -> AsyncGenerator[Context]:
        events.append("context:enter")
        try:
            yield Context(events)
        finally:
            events.append("context:exit")

    def observe(outcome: UseCaseOutcome) -> None:
        events.append(f"observe:{outcome.entrypoint}:{outcome.status}")
        outcomes.append(outcome)

    runner = create_tool_runner(
        tools=tool_group(
            tool_handler(
                tool(
                    "system.ping",
                    result=str,
                    description="Ping.",
                    read_only=True,
                ),
                ping,
            )
        ),
        context_factory=context_factory,
        use_case_observers=(observe,),
    )

    assert await runner.call("system.ping") == "pong"
    assert events == [
        "context:enter",
        "ping",
        "context:exit",
        "observe:tool:succeeded",
    ]
    assert outcomes[0].use_case is ping


async def test_cancellation_propagates_through_cleanup_and_observers() -> None:
    events: list[str] = []
    outcomes: list[UseCaseOutcome] = []
    started = asyncio.Event()

    async def wait(context: Context) -> None:
        context.events.append("run")
        started.set()
        await asyncio.Event().wait()

    @asynccontextmanager
    async def context_factory() -> AsyncGenerator[Context]:
        events.append("context:enter")
        try:
            yield Context(events)
        except BaseException:
            events.append("context:rollback")
            raise

    runner = create_tool_runner(
        tools=tool_group(
            tool_handler(
                tool(
                    "system.wait",
                    result=None,
                    description="Wait forever.",
                    read_only=True,
                ),
                wait,
            )
        ),
        context_factory=context_factory,
        use_case_observers=(outcomes.append,),
    )

    pending = asyncio.create_task(runner.call("system.wait"))
    await started.wait()
    pending.cancel()
    with pytest.raises(asyncio.CancelledError):
        await pending

    assert events == ["context:enter", "run", "context:rollback"]
    assert outcomes[0].entrypoint == "tool"
    assert outcomes[0].status == "cancelled"


def test_manifest_is_canonical_and_contains_only_portable_contract_data() -> None:
    first_error = ErrorDef(code="A_FIRST", status=400, message="First")
    last_error = ErrorDef(code="Z_LAST", status=409, message="Last")
    search_binding = tool_handler(
        search_tool(errors=(last_error, first_error)),
        search,
    )
    ping_binding = tool_handler(
        tool(
            "system.ping",
            result=str,
            description="Ping.",
            read_only=True,
            open_world=False,
        ),
        ping,
    )

    manifest = tool_manifest(tool_group(ping_binding, search_binding))
    reversed_manifest = tool_manifest(tool_group(search_binding, ping_binding))

    assert manifest == reversed_manifest
    assert json.loads(json.dumps(manifest, sort_keys=True)) == manifest
    assert manifest["schema_version"] == 1
    assert [entry["name"] for entry in manifest["tools"]] == [
        "projects.search",
        "system.ping",
    ]
    search_entry = manifest["tools"][0]
    assert search_entry["input_schema"]["type"] == "object"
    assert search_entry["output_schema"]["type"] == "object"
    assert [error["code"] for error in search_entry["errors"]] == [
        "A_FIRST",
        "Z_LAST",
    ]
    assert search_entry["annotations"] == {
        "destructive": False,
        "idempotent": True,
        "open_world": False,
        "read_only": True,
    }
    assert manifest["tools"][1]["input_schema"] == {
        "additionalProperties": False,
        "properties": {},
        "type": "object",
    }


@dataclass(frozen=True, slots=True)
class _ManifestProtocolChange:
    severity: ChangeSeverity
    location: str
    message: str


class _ManifestChangeSink:
    def __init__(self) -> None:
        self.changes: list[_ManifestProtocolChange] = []

    def add(self, severity: ChangeSeverity, location: str, message: str) -> None:
        self.changes.append(_ManifestProtocolChange(severity, location, message))


def _render_manifest_protocol() -> dict[str, object]:
    adapter = TypeAdapter(ToolManifest)
    adapter.validate_python(
        {
            "schema_version": TOOL_MANIFEST_VERSION,
            "tools": [],
        },
        strict=True,
    )
    schema = adapter.json_schema(mode="serialization")
    return {
        "schema_version": TOOL_MANIFEST_VERSION,
        "manifest_schema": schema,
    }


def _load_manifest_protocol() -> dict[str, object]:
    value: object = json.loads(TOOL_MANIFEST_SNAPSHOT_PATH.read_text(encoding="utf-8"))
    assert isinstance(value, dict)
    mapping = cast(dict[object, object], value)
    assert all(isinstance(key, str) for key in mapping)
    return cast(dict[str, object], mapping)


def _object(value: object) -> dict[str, object]:
    assert isinstance(value, dict)
    mapping = cast(dict[object, object], value)
    assert all(isinstance(key, str) for key in mapping)
    return cast(dict[str, object], mapping)


def _manifest_protocol_incompatibilities(
    baseline: dict[str, object],
    current: dict[str, object],
) -> list[_ManifestProtocolChange]:
    baseline_schema = _object(baseline["manifest_schema"])
    current_schema = _object(current["manifest_schema"])
    sink = _ManifestChangeSink()
    compare_schema(
        _without_definitions(baseline_schema),
        _without_definitions(current_schema),
        direction="output",
        baseline=baseline_schema,
        current=current_schema,
        location="tool manifest",
        changes=sink,
    )
    return [
        change for change in sink.changes if change.severity in ("breaking", "unknown")
    ]


def _without_definitions(schema: dict[str, object]) -> dict[str, object]:
    return {key: value for key, value in schema.items() if key != "$defs"}


def _manifest_snapshot_version(path: Path) -> int | None:
    prefix = "tool_manifest_protocol_v"
    suffix = "_snapshot.json"
    if not path.name.startswith(prefix) or not path.name.endswith(suffix):
        return None
    raw_version = path.name[len(prefix) : -len(suffix)]
    return int(raw_version) if raw_version.isdigit() else None


def _versioned_manifest_snapshot_paths() -> dict[int, Path]:
    snapshots: dict[int, Path] = {}
    for path in Path(__file__).parent.glob("tool_manifest_protocol_v*_snapshot.json"):
        version = _manifest_snapshot_version(path)
        assert version is not None, f"Invalid tool manifest snapshot: {path.name}"
        assert version not in snapshots
        snapshots[version] = path
    return snapshots


def _git_manifest_snapshot_texts(base_ref: str) -> dict[int, str]:
    listed = subprocess.run(
        ["git", "ls-tree", "-r", "--name-only", base_ref, "--", "tests"],
        cwd=REPOSITORY_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert listed.returncode == 0, (
        f"Could not inspect tool manifest snapshots at {base_ref!r}: "
        f"{listed.stderr.strip()}"
    )

    snapshots: dict[int, str] = {}
    for relative in listed.stdout.splitlines():
        path = Path(relative)
        version = _manifest_snapshot_version(path)
        if path.parent != Path("tests") or version is None:
            continue
        shown = subprocess.run(
            ["git", "show", f"{base_ref}:{relative}"],
            cwd=REPOSITORY_ROOT,
            capture_output=True,
            text=True,
            check=False,
        )
        assert shown.returncode == 0, (
            f"Could not read {relative} at {base_ref!r}: {shown.stderr.strip()}"
        )
        snapshots[version] = shown.stdout
    return snapshots


def test_tool_manifest_protocol_matches_its_versioned_snapshot() -> None:
    current = _render_manifest_protocol()

    if os.environ.get(UPDATE_TOOL_MANIFEST_SNAPSHOT):
        if TOOL_MANIFEST_SNAPSHOT_PATH.exists():
            incompatible = _manifest_protocol_incompatibilities(
                _load_manifest_protocol(),
                current,
            )
            assert not incompatible, (
                "Breaking or unknown tool-manifest changes require bumping "
                "TOOL_MANIFEST_VERSION and creating a new snapshot: "
                f"{incompatible}"
            )
        TOOL_MANIFEST_SNAPSHOT_PATH.write_text(
            json.dumps(current, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )

    assert TOOL_MANIFEST_SNAPSHOT_PATH.exists(), (
        f"No tool manifest v{TOOL_MANIFEST_VERSION} snapshot found. Generate "
        f"one with {UPDATE_TOOL_MANIFEST_SNAPSHOT}=1 uv run pytest "
        "tests/test_tools.py"
    )
    baseline = _load_manifest_protocol()
    incompatible = _manifest_protocol_incompatibilities(baseline, current)
    assert not incompatible, (
        "Breaking or unknown tool-manifest changes require bumping "
        f"TOOL_MANIFEST_VERSION: {incompatible}"
    )
    assert current == baseline, (
        "The tool manifest changed without updating its canonical snapshot. "
        f"If the change is additive, regenerate with "
        f"{UPDATE_TOOL_MANIFEST_SNAPSHOT}=1 uv run pytest tests/test_tools.py. "
        "Breaking or unknown changes require a new protocol version."
    )


def test_tool_manifest_snapshot_history_is_complete() -> None:
    snapshots = _versioned_manifest_snapshot_paths()

    assert set(snapshots) == set(range(1, TOOL_MANIFEST_VERSION + 1))


def test_tool_manifest_protocol_matches_its_git_baseline() -> None:
    base_ref = os.environ.get(TOOL_MANIFEST_BASE_REF)
    if base_ref is None:
        return

    baseline_snapshots = _git_manifest_snapshot_texts(base_ref)
    if not baseline_snapshots:
        assert TOOL_MANIFEST_VERSION == 1
        return

    previous_version = max(baseline_snapshots)
    assert set(baseline_snapshots) == set(range(1, previous_version + 1))
    assert TOOL_MANIFEST_VERSION in (previous_version, previous_version + 1)

    local_snapshots = _versioned_manifest_snapshot_paths()
    for version, baseline_text in baseline_snapshots.items():
        if version >= TOOL_MANIFEST_VERSION:
            continue
        assert version in local_snapshots
        assert local_snapshots[version].read_text(encoding="utf-8") == baseline_text, (
            f"Historical tool manifest v{version} must remain byte-for-byte stable"
        )

    if previous_version != TOOL_MANIFEST_VERSION:
        return

    baseline_value: object = json.loads(baseline_snapshots[TOOL_MANIFEST_VERSION])
    incompatible = _manifest_protocol_incompatibilities(
        _object(baseline_value),
        _render_manifest_protocol(),
    )
    assert not incompatible, (
        "Breaking or unknown tool-manifest changes require bumping "
        f"TOOL_MANIFEST_VERSION: {incompatible}"
    )


def test_same_manifest_version_rejects_breaking_changes() -> None:
    baseline = _render_manifest_protocol()
    current_value: object = json.loads(json.dumps(baseline))
    current = _object(current_value)
    schema = _object(current["manifest_schema"])
    properties = _object(schema["properties"])
    properties.pop("tools")

    incompatible = _manifest_protocol_incompatibilities(
        baseline,
        current,
    )

    assert any(change.severity == "breaking" for change in incompatible)
