"""Optional MCP transport for authenticated Tenchi application tools.

Install the ``mcp`` extra before importing this module. The adapter exposes a
registered :class:`~tenchi.tools.ToolGroup` through the Model Context Protocol
without moving authentication, authorization, approval, or application
lifecycle into tool declarations.

Each MCP request is authenticated independently. Discovery and invocation use
the same optional visibility filter, destructive calls are denied unless an
application approval callback accepts them, and execution delegates to a
caller-specific :class:`~tenchi.tools.ToolRunner`.

Network serving supports stateless Streamable HTTP only. The MCP SDK's legacy
SSE entrypoints are rejected because they do not preserve Tenchi's per-request
header authentication scope.
"""

from __future__ import annotations

import inspect
import json
from asyncio import CancelledError
from collections.abc import Awaitable, Callable, Mapping
from contextvars import ContextVar
from copy import deepcopy
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Any, Literal, cast

from jsonschema import Draft202012Validator, FormatChecker
from jsonschema.exceptions import ValidationError as JsonSchemaValidationError
from jsonschema.protocols import Validator
from pydantic import ValidationError
from pydantic_core import to_jsonable_python
from starlette.applications import Starlette
from starlette.types import ASGIApp, Receive, Scope, Send

try:
    from mcp.server.context import CallNext, HandlerResult, ServerRequestContext
    from mcp.server.mcpserver import Context as McpContext
    from mcp.server.mcpserver import MCPServer
    from mcp.server.mcpserver.exceptions import ToolError
    from mcp.server.streamable_http import EventStore
    from mcp.server.transport_security import TransportSecuritySettings
    from mcp.shared.exceptions import MCPError
    from mcp.types import (
        INTERNAL_ERROR,
        CallToolResult,
        InputRequiredResult,
        TextContent,
        ToolAnnotations,
    )
    from mcp.types import Tool as McpTool
except ImportError as exc:
    if exc.name == "mcp" or (exc.name is not None and exc.name.startswith("mcp.")):
        raise ImportError(
            "tenchi.mcp requires Model Context Protocol support; "
            'run: uv add "tenchi[mcp]"'
        ) from exc
    raise

from . import __version__
from .errors import AppError, ConfigurationError
from .tools import (
    Tool,
    ToolBinding,
    ToolErrorManifest,
    ToolGroup,
    ToolInvocationError,
    ToolResultError,
    ToolRunner,
    _tool_kwargs,  # pyright: ignore[reportPrivateUsage]
    tool_manifest,
)

TOOL_MCP_PROTOCOL_VERSION = 2

type _McpFailureKind = Literal[
    "invalid_input",
    "approval_required",
    "approval_denied",
    "application_error",
    "invalid_result",
    "failed",
]

_ERROR_KINDS: tuple[_McpFailureKind, ...] = (
    "invalid_input",
    "approval_required",
    "approval_denied",
    "application_error",
    "invalid_result",
    "failed",
)

_LEGACY_SSE_UNSUPPORTED = (
    "Tenchi application MCP does not support legacy SSE; use Streamable HTTP"
)
_DISCOVERY_FAILED = "Tool discovery failed."


@dataclass(frozen=True, slots=True)
class McpRequest:
    """Transport context supplied to application authentication.

    ``headers`` is a detached, read-only copy of the request headers for HTTP
    transports, with lowercase names, and ``None`` for stdio or direct
    in-process calls. Applications may inspect it to authenticate a caller,
    but tool input never supplies identity.
    """

    request_id: str | int | None
    headers: Mapping[str, str] | None = field(repr=False)

    def __post_init__(self) -> None:
        if self.headers is not None:
            object.__setattr__(
                self,
                "headers",
                MappingProxyType(
                    {key.lower(): value for key, value in self.headers.items()}
                ),
            )


@dataclass(frozen=True, slots=True)
class _McpEntry:
    binding: ToolBinding[Any, Any]
    definition: McpTool
    input_validator: Validator
    direct_input: bool


class _HttpRequestScope:
    def __init__(
        self,
        app: ASGIApp,
        *,
        request: ContextVar[McpRequest | None],
    ) -> None:
        self._app = app
        self._request = request

    async def __call__(
        self,
        scope: Scope,
        receive: Receive,
        send: Send,
    ) -> None:
        if scope["type"] != "http":
            await self._app(scope, receive, send)
            return
        headers = MappingProxyType(
            {
                key.decode("latin-1").lower(): value.decode("latin-1")
                for key, value in scope.get("headers", ())
            }
        )
        token = self._request.set(McpRequest(request_id=None, headers=headers))
        try:
            await self._app(scope, receive, send)
        finally:
            self._request.reset(token)


class _ToolMcpServer[PrincipalT](MCPServer[dict[str, Any]]):
    def __init__(
        self,
        *,
        tools: ToolGroup,
        authenticate: Callable[
            [McpRequest],
            PrincipalT | Awaitable[PrincipalT],
        ],
        runner_factory: Callable[
            [PrincipalT],
            ToolRunner | Awaitable[ToolRunner],
        ],
        allow_tool: (
            Callable[
                [PrincipalT, Tool[Any, Any]],
                bool | Awaitable[bool],
            ]
            | None
        ),
        approve: (
            Callable[
                [PrincipalT, Tool[Any, Any], object],
                bool | Awaitable[bool],
            ]
            | None
        ),
        name: str,
        instructions: str | None,
        website_url: str | None,
        transport_security: TransportSecuritySettings | None,
    ) -> None:
        self._application_tools = tools
        self._authenticate = authenticate
        self._runner_factory = runner_factory
        self._allow_tool = allow_tool
        self._approve = approve
        self._entries = _mcp_entries(tools)
        self._transport_security = transport_security
        self._request_scope: ContextVar[McpRequest | None] = ContextVar(
            "tenchi_mcp_request",
            default=None,
        )
        super().__init__(
            name,
            instructions=instructions,
            website_url=website_url,
            version=__version__,
            middleware=(self._capture_request,),
        )

    async def _capture_request(
        self,
        ctx: ServerRequestContext[Any, Any],
        call_next: CallNext,
    ) -> HandlerResult:
        current = self._request_scope.get()
        request = ctx.request
        raw_request_id = ctx.request_id
        headers: Mapping[str, str] | None = None
        if request is not None and hasattr(request, "headers"):
            headers = MappingProxyType(dict(request.headers))
        elif current is not None:
            headers = current.headers
        captured = McpRequest(
            request_id=(
                raw_request_id if isinstance(raw_request_id, str | int) else None
            ),
            headers=headers,
        )
        token = self._request_scope.set(captured)
        try:
            return await call_next(ctx)
        finally:
            self._request_scope.reset(token)

    def streamable_http_app(
        self,
        *,
        streamable_http_path: str = "/mcp",
        json_response: bool = False,
        stateless_http: bool = True,
        event_store: EventStore | None = None,
        retry_interval: int | None = None,
        max_request_body_size: int = 4 * 1024 * 1024,
        transport_security: TransportSecuritySettings | None = None,
        host: str = "127.0.0.1",
    ) -> Starlette:
        _require_stateless_http(stateless_http)
        app = super().streamable_http_app(
            streamable_http_path=streamable_http_path,
            json_response=json_response,
            stateless_http=stateless_http,
            event_store=event_store,
            retry_interval=retry_interval,
            max_request_body_size=max_request_body_size,
            transport_security=(
                self._transport_security
                if transport_security is None
                else transport_security
            ),
            host=host,
        )
        app.add_middleware(_HttpRequestScope, request=self._request_scope)
        return app

    async def run_streamable_http_async(
        self,
        *,
        host: str = "127.0.0.1",
        port: int = 8000,
        streamable_http_path: str = "/mcp",
        json_response: bool = False,
        stateless_http: bool = True,
        event_store: EventStore | None = None,
        retry_interval: int | None = None,
        max_request_body_size: int = 4 * 1024 * 1024,
        transport_security: TransportSecuritySettings | None = None,
    ) -> None:
        _require_stateless_http(stateless_http)
        await super().run_streamable_http_async(
            host=host,
            port=port,
            streamable_http_path=streamable_http_path,
            json_response=json_response,
            stateless_http=stateless_http,
            event_store=event_store,
            retry_interval=retry_interval,
            max_request_body_size=max_request_body_size,
            transport_security=(
                self._transport_security
                if transport_security is None
                else transport_security
            ),
        )

    def sse_app(
        self,
        *,
        sse_path: str = "/sse",
        message_path: str = "/messages/",
        transport_security: TransportSecuritySettings | None = None,
        host: str = "127.0.0.1",
    ) -> Starlette:
        """Reject legacy SSE, which lacks Tenchi's HTTP request scope."""
        del sse_path, message_path, transport_security, host
        raise ConfigurationError(_LEGACY_SSE_UNSUPPORTED)

    async def run_sse_async(
        self,
        *,
        host: str = "127.0.0.1",
        port: int = 8000,
        sse_path: str = "/sse",
        message_path: str = "/messages/",
        transport_security: TransportSecuritySettings | None = None,
    ) -> None:
        """Reject legacy SSE, which lacks Tenchi's HTTP request scope."""
        del host, port, sse_path, message_path, transport_security
        raise ConfigurationError(_LEGACY_SSE_UNSUPPORTED)

    async def list_tools(self) -> list[McpTool]:
        try:
            principal = await self._principal()
            visible: list[McpTool] = []
            for entry in self._entries.values():
                if await self._is_allowed(principal, entry.binding.tool):
                    visible.append(entry.definition)
            return visible
        except ToolError as exc:
            raise MCPError(code=INTERNAL_ERROR, message=_DISCOVERY_FAILED) from exc

    async def call_tool(
        self,
        name: str,
        arguments: dict[str, Any],
        context: McpContext[dict[str, Any], Any] | None = None,
    ) -> CallToolResult | InputRequiredResult:
        del context
        principal = await self._principal()
        entry = self._entries.get(name)
        if entry is None or not await self._is_allowed(
            principal,
            entry.binding.tool,
        ):
            raise ToolError(f"unknown tool {name!r}")

        try:
            entry.input_validator.validate(arguments)
        except JsonSchemaValidationError:
            return _result(
                _failure(
                    "invalid_input",
                    "Input does not match the tool schema.",
                )
            )

        declaration = entry.binding.tool
        input_value: object = arguments if entry.direct_input else arguments["input"]
        try:
            kwargs = _tool_kwargs(
                declaration,
                input=input_value,
                input_json=None,
            )
        except CancelledError:
            raise
        except KeyboardInterrupt:
            raise
        except ValidationError:
            return _result(
                _failure(
                    "invalid_input",
                    "Input does not match the tool schema.",
                )
            )
        except Exception:
            return _result(
                _failure(
                    "failed",
                    "The tool invocation failed.",
                )
            )

        approval_input: object = {}
        if declaration.destructive:
            if self._approve is None:
                return _result(
                    _failure(
                        "approval_required",
                        "This destructive tool requires explicit approval.",
                    )
                )
            try:
                approval_input = _approval_input(declaration, kwargs)
            except CancelledError:
                raise
            except KeyboardInterrupt:
                raise
            except Exception:
                return _result(
                    _failure(
                        "failed",
                        "The tool invocation failed.",
                    )
                )

        runner = await self._runner(principal)
        if declaration.destructive:
            approved = await self._approved(
                principal,
                declaration,
                deepcopy(approval_input),
            )
            if not approved:
                return _result(
                    _failure(
                        "approval_denied",
                        "The destructive tool call was not approved.",
                    )
                )

        try:
            serialized = await runner._call_prepared_serialized(  # pyright: ignore[reportPrivateUsage]
                name,
                kwargs=kwargs,
            )
        except CancelledError:
            raise
        except KeyboardInterrupt:
            raise
        except ValidationError:
            return _result(
                _failure(
                    "invalid_input",
                    "Input does not match the tool schema.",
                )
            )
        except AppError as exc:
            return _result(
                _failure(
                    "application_error",
                    exc.message,
                    code=exc.code,
                    details=_jsonable_details(exc.details),
                )
            )
        except ToolResultError:
            return _result(
                _failure(
                    "invalid_result",
                    "The tool returned an invalid result.",
                )
            )
        except ToolInvocationError:
            return _result(
                _failure(
                    "failed",
                    "The tool invocation failed.",
                )
            )
        except Exception:
            return _result(
                _failure(
                    "failed",
                    "The tool invocation failed.",
                )
            )

        return _result(
            {
                "schema_version": TOOL_MCP_PROTOCOL_VERSION,
                "ok": True,
                "result": serialized,
            }
        )

    def _request(self) -> McpRequest:
        return self._request_scope.get() or McpRequest(
            request_id=None,
            headers=None,
        )

    async def _principal(self) -> PrincipalT:
        try:
            return cast(
                PrincipalT,
                await _resolve(self._authenticate(self._request())),
            )
        except CancelledError:
            raise
        except KeyboardInterrupt:
            raise
        except Exception as exc:
            raise ToolError("authentication failed") from exc

    async def _is_allowed(
        self,
        principal: PrincipalT,
        declaration: Tool[Any, Any],
    ) -> bool:
        if self._allow_tool is None:
            return True
        try:
            allowed = await _resolve(self._allow_tool(principal, declaration))
        except CancelledError:
            raise
        except KeyboardInterrupt:
            raise
        except Exception as exc:
            raise ToolError("tool visibility check failed") from exc
        if not isinstance(allowed, bool):
            raise ToolError("tool visibility check returned an invalid result")
        return allowed

    async def _approved(
        self,
        principal: PrincipalT,
        declaration: Tool[Any, Any],
        input_value: object,
    ) -> bool:
        assert self._approve is not None
        try:
            approved = await _resolve(
                self._approve(principal, declaration, input_value)
            )
        except CancelledError:
            raise
        except KeyboardInterrupt:
            raise
        except Exception as exc:
            raise ToolError("tool approval failed") from exc
        if not isinstance(approved, bool):
            raise ToolError("tool approval returned an invalid result")
        return approved

    async def _runner(self, principal: PrincipalT) -> ToolRunner:
        try:
            runner = await _resolve(self._runner_factory(principal))
        except CancelledError:
            raise
        except KeyboardInterrupt:
            raise
        except Exception as exc:
            raise ToolError("tool runner creation failed") from exc
        if (
            not isinstance(runner, ToolRunner)
            or runner.tools != self._application_tools
        ):
            raise ToolError("tool runner is misconfigured")
        return runner


def create_tool_mcp_server[PrincipalT](
    *,
    tools: ToolGroup,
    authenticate: Callable[
        [McpRequest],
        PrincipalT | Awaitable[PrincipalT],
    ],
    runner_factory: Callable[
        [PrincipalT],
        ToolRunner | Awaitable[ToolRunner],
    ],
    allow_tool: (
        Callable[
            [PrincipalT, Tool[Any, Any]],
            bool | Awaitable[bool],
        ]
        | None
    ) = None,
    approve: (
        Callable[
            [PrincipalT, Tool[Any, Any], object],
            bool | Awaitable[bool],
        ]
        | None
    ) = None,
    name: str = "Tenchi application",
    instructions: str | None = None,
    website_url: str | None = None,
    transport_security: TransportSecuritySettings | None = None,
) -> MCPServer[dict[str, Any]]:
    """Expose application tools through an authenticated MCP server.

    Authentication runs for both discovery and invocation. ``allow_tool`` may
    hide tools per principal and is rechecked before every call. It is not
    business authorization: use cases still enforce application policy.

    Destructive tools are denied unless ``approve`` is provided and returns
    ``True`` for that exact principal, declaration, and normalized JSON
    representation of the validated input.

    Pass the MCP SDK's ``TransportSecuritySettings`` when serving Streamable
    HTTP on a non-loopback host. The SDK's default policy allows loopback
    development hosts only. Legacy SSE serving is intentionally unsupported;
    use ``streamable_http_app()`` or ``run_streamable_http_async()``.
    """
    raw_tools = cast(object, tools)
    if not isinstance(raw_tools, ToolGroup):
        raise ConfigurationError(
            "create_tool_mcp_server: tools must be a ToolGroup, "
            f"got {type(tools).__name__}"
        )
    _require_callback(
        authenticate,
        arguments=1,
        label="create_tool_mcp_server: authenticate",
    )
    _require_callback(
        runner_factory,
        arguments=1,
        label="create_tool_mcp_server: runner_factory",
    )
    if allow_tool is not None:
        _require_callback(
            allow_tool,
            arguments=2,
            label="create_tool_mcp_server: allow_tool",
        )
    if approve is not None:
        _require_callback(
            approve,
            arguments=3,
            label="create_tool_mcp_server: approve",
        )
    raw_name = cast(object, name)
    if not isinstance(raw_name, str) or not raw_name.strip():
        raise ConfigurationError(
            "create_tool_mcp_server: name must be a non-empty string"
        )
    for label, value in (
        ("instructions", instructions),
        ("website_url", website_url),
    ):
        raw_value = cast(object, value)
        if raw_value is not None and not isinstance(raw_value, str):
            raise ConfigurationError(
                f"create_tool_mcp_server: {label} must be a string or None"
            )
    raw_transport_security = cast(object, transport_security)
    if raw_transport_security is not None and not isinstance(
        raw_transport_security,
        TransportSecuritySettings,
    ):
        raise ConfigurationError(
            "create_tool_mcp_server: transport_security must be a "
            "TransportSecuritySettings value or None"
        )

    return _ToolMcpServer(
        tools=raw_tools,
        authenticate=authenticate,
        runner_factory=runner_factory,
        allow_tool=allow_tool,
        approve=approve,
        name=raw_name.strip(),
        instructions=instructions,
        website_url=website_url,
        transport_security=transport_security,
    )


def _require_stateless_http(value: object) -> None:
    if value is not True:
        raise ConfigurationError(
            "Tenchi application MCP requires stateless_http=True so each "
            "request is authenticated independently"
        )


def _mcp_entries(tools: ToolGroup) -> dict[str, _McpEntry]:
    manifest = tool_manifest(tools)
    manifests = {entry["name"]: entry for entry in manifest["tools"]}
    entries: dict[str, _McpEntry] = {}
    for binding in sorted(tools, key=lambda item: item.tool.name):
        declaration = binding.tool
        manifest_entry = manifests[declaration.name]
        input_schema, direct_input = _mcp_input_schema(
            manifest_entry["input_schema"],
            no_input=declaration.request is None,
        )
        output_schema = _mcp_output_schema(
            manifest_entry["output_schema"],
            errors=manifest_entry["errors"],
        )
        Draft202012Validator.check_schema(input_schema)
        Draft202012Validator.check_schema(output_schema)
        entries[declaration.name] = _McpEntry(
            binding=binding,
            definition=McpTool(
                name=declaration.name,
                description=declaration.description,
                input_schema=input_schema,
                output_schema=output_schema,
                annotations=ToolAnnotations(
                    read_only_hint=declaration.read_only,
                    destructive_hint=declaration.destructive,
                    idempotent_hint=declaration.idempotent,
                    open_world_hint=declaration.open_world,
                ),
            ),
            input_validator=Draft202012Validator(
                input_schema,
                format_checker=FormatChecker(),
            ),
            direct_input=direct_input,
        )
    return entries


def _approval_input(
    declaration: Tool[Any, Any],
    kwargs: dict[str, Any],
) -> Any:
    adapter = (
        declaration._request_adapter  # pyright: ignore[reportPrivateUsage]
    )
    if adapter is None:
        return {}
    value = adapter.dump_python(
        kwargs["request"],
        mode="json",
        by_alias=True,
        warnings="error",
    )
    json.dumps(value, allow_nan=False)
    return value


def _jsonable_details(value: Any) -> Any:
    if value is None:
        return None
    try:
        converted = to_jsonable_python(value, fallback=str)
        json.dumps(converted, allow_nan=False)
    except Exception:
        return None
    return converted


def _mcp_input_schema(
    schema: dict[str, Any],
    *,
    no_input: bool,
) -> tuple[dict[str, Any], bool]:
    copied = deepcopy(schema)
    direct = _direct_object_schema(copied)
    if no_input or direct is not None:
        if direct is not None:
            copied = direct
        return copied, True
    definitions, reference = _embedded_schema(copied, prefix="tool_input")
    return (
        {
            "$defs": definitions,
            "type": "object",
            "properties": {"input": reference},
            "required": ["input"],
            "additionalProperties": False,
        },
        False,
    )


def _direct_object_schema(schema: dict[str, Any]) -> dict[str, Any] | None:
    if schema.get("type") == "object":
        return schema
    reference = schema.get("$ref")
    raw_definitions = schema.get("$defs")
    if (
        not isinstance(reference, str)
        or not reference.startswith("#/$defs/")
        or not isinstance(raw_definitions, dict)
    ):
        return None
    definitions = cast(dict[str, Any], raw_definitions)
    name = reference.removeprefix("#/$defs/").replace("~1", "/").replace("~0", "~")
    raw_target = definitions.get(name)
    if not isinstance(raw_target, dict):
        return None
    target = cast(dict[str, Any], raw_target)
    if target.get("type") != "object":
        return None

    direct = deepcopy(schema)
    direct["type"] = "object"
    return direct


def _mcp_output_schema(
    schema: dict[str, Any],
    *,
    errors: list[ToolErrorManifest],
) -> dict[str, Any]:
    definitions, result_reference = _embedded_schema(
        deepcopy(schema),
        prefix="tool_result",
    )
    definitions["tool_error"] = _mcp_error_schema(errors)
    version = {
        "type": "integer",
        "const": TOOL_MCP_PROTOCOL_VERSION,
    }
    return {
        "$defs": definitions,
        "type": "object",
        "oneOf": [
            {
                "type": "object",
                "properties": {
                    "schema_version": version,
                    "ok": {"type": "boolean", "const": True},
                    "result": result_reference,
                },
                "required": ["schema_version", "ok", "result"],
                "additionalProperties": False,
            },
            {
                "type": "object",
                "properties": {
                    "schema_version": version,
                    "ok": {"type": "boolean", "const": False},
                    "error": {"$ref": "#/$defs/tool_error"},
                },
                "required": ["schema_version", "ok", "error"],
                "additionalProperties": False,
            },
        ],
    }


def _mcp_error_schema(errors: list[ToolErrorManifest]) -> dict[str, Any]:
    variants: list[dict[str, Any]] = [
        {
            "type": "object",
            "properties": {
                "kind": {"type": "string", "const": kind},
                "message": {"type": "string"},
            },
            "required": ["kind", "message"],
            "additionalProperties": False,
        }
        for kind in _ERROR_KINDS
        if kind != "application_error"
    ]
    if errors:
        variants.append(
            {
                "type": "object",
                "properties": {
                    "kind": {
                        "type": "string",
                        "const": "application_error",
                    },
                    "code": {
                        "type": "string",
                        "enum": [error["code"] for error in errors],
                    },
                    "message": {"type": "string"},
                    "details": {},
                },
                "required": ["kind", "code", "message"],
                "additionalProperties": False,
            }
        )
    return {"oneOf": variants}


def _embedded_schema(
    schema: dict[str, Any],
    *,
    prefix: str,
) -> tuple[dict[str, Any], dict[str, str]]:
    original_definitions = cast(dict[str, Any], schema.pop("$defs", {}))
    definitions: dict[str, Any] = {}
    for name, value in original_definitions.items():
        definitions[f"{prefix}_definition_{name}"] = _replace_definition_refs(
            value,
            prefix=prefix,
        )
    definitions[f"{prefix}_value"] = _replace_definition_refs(
        schema,
        prefix=prefix,
    )
    return definitions, {"$ref": f"#/$defs/{prefix}_value"}


def _replace_definition_refs(value: Any, *, prefix: str) -> Any:
    if isinstance(value, dict):
        mapping = cast(dict[str, Any], value)
        return {
            key: (
                item.replace(
                    "#/$defs/",
                    f"#/$defs/{prefix}_definition_",
                    1,
                )
                if key in ("$ref", "$dynamicRef")
                and isinstance(item, str)
                and item.startswith("#/$defs/")
                else _replace_definition_refs(item, prefix=prefix)
            )
            for key, item in mapping.items()
        }
    if isinstance(value, list):
        sequence = cast(list[Any], value)
        return [_replace_definition_refs(item, prefix=prefix) for item in sequence]
    return value


def _failure(
    kind: _McpFailureKind,
    message: str,
    *,
    code: str | None = None,
    details: Any = None,
) -> dict[str, Any]:
    error: dict[str, Any] = {
        "kind": kind,
        "message": message,
    }
    if code is not None:
        error["code"] = code
    if details is not None:
        error["details"] = details
    return {
        "schema_version": TOOL_MCP_PROTOCOL_VERSION,
        "ok": False,
        "error": error,
    }


def _result(payload: dict[str, Any]) -> CallToolResult:
    return CallToolResult(
        content=[
            TextContent(
                type="text",
                text=json.dumps(payload, sort_keys=True, separators=(",", ":")),
            )
        ],
        structured_content=payload,
        is_error=payload["ok"] is False,
    )


async def _resolve(value: Any) -> Any:
    return await value if inspect.isawaitable(value) else value


def _require_callback(
    value: object,
    *,
    arguments: int,
    label: str,
) -> None:
    try:
        signature = inspect.signature(cast(Callable[..., object], value))
        signature.bind(*(object() for _ in range(arguments)))
    except (TypeError, ValueError) as exc:
        raise ConfigurationError(
            f"{label} must accept {arguments} positional argument(s): {exc}"
        ) from exc


__all__ = [
    "TOOL_MCP_PROTOCOL_VERSION",
    "McpRequest",
    "create_tool_mcp_server",
]
