"""Typed HTTP client driven by the same contracts as the server.

:class:`Client` sends a request described by a contract and returns the
validated response type, so callers get real static types from the single
source of truth::

    async with Client(base_url="http://localhost:8000") as client:
        todo = await client.call(create_todo_contract, request=CreateTodo(...))

Error semantics mirror the server: an error response whose code and status
match one of the contract's declared errors is raised as :class:`AppError`
carrying that definition; anything else raises
:class:`UnexpectedResponseError`.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from types import TracebackType
from typing import Any, Self, cast
from urllib.parse import quote

import httpx
from pydantic import TypeAdapter

from .contracts import (
    _PATH_PARAMETER,  # pyright: ignore[reportPrivateUsage]
    Contract,
    ResponseHeadersT,
    ResponseT,
    _is_json_media_type,  # pyright: ignore[reportPrivateUsage]
    _is_text_media_type,  # pyright: ignore[reportPrivateUsage]
    _object_schema,  # pyright: ignore[reportPrivateUsage]
    _response_header_fields,  # pyright: ignore[reportPrivateUsage]
)
from .errors import (
    ERROR_SOURCE_HEADER as _ERROR_SOURCE_HEADER,
)
from .errors import (
    AppError,
    ConfigurationError,
    ErrorDef,
    TenchiError,
    _validated_error_defs,  # pyright: ignore[reportPrivateUsage]
)

_adapters: dict[Any, TypeAdapter[Any]] = {}


class _Unset:
    def __repr__(self) -> str:
        return "UNSET"


_UNSET = _Unset()


class UnexpectedResponseError(TenchiError):
    """A response that matched neither the contract's success status nor a
    declared error."""

    def __init__(self, *, contract_name: str, status_code: int, body: Any) -> None:
        super().__init__(f"{contract_name} returned unexpected status {status_code}")
        self.contract_name = contract_name
        self.status_code = status_code
        self.body = body


@dataclass(frozen=True, slots=True)
class ClientResponse[BodyT, HeadersT]:
    """A validated success value together with its declared headers and
    underlying httpx response."""

    body: BodyT
    headers: HeadersT
    http_response: httpx.Response


class Client:
    """Contract-driven HTTP client over ``httpx.AsyncClient``.

    The common constructions own their transport — the client closes it on
    ``aclose`` (or on exiting ``async with``):

        Client(base_url="http://localhost:8000")
        Client(base_url=..., headers={"authorization": "Bearer ..."})
        Client(transport=httpx.ASGITransport(app=app))  # in-process tests

    ``headers`` are sent on every request (per-call ``headers=`` models
    override them per name). When only ``transport`` is given, ``base_url``
    defaults to ``http://testserver``.

    Alternatively pass a fully configured ``http=httpx.AsyncClient``; the
    caller keeps ownership and ``aclose`` leaves it open. ``http`` is
    mutually exclusive with ``base_url``, ``headers``, and ``transport``.

    ``errors`` declares expected errors for every call — the client-side
    counterpart of ``route_group(errors=...)`` for errors the server's
    hooks may raise on any route, such as an authentication failure.
    """

    def __init__(
        self,
        *,
        base_url: str | None = None,
        headers: Mapping[str, str] | None = None,
        transport: httpx.AsyncBaseTransport | None = None,
        http: httpx.AsyncClient | None = None,
        errors: Sequence[ErrorDef] = (),
    ) -> None:
        declared_errors = _validated_error_defs(errors, label="Client errors")
        if http is not None:
            if base_url is not None or headers is not None or transport is not None:
                raise ConfigurationError(
                    "Client: http= is mutually exclusive with base_url=, "
                    "headers=, and transport=; configure the httpx client "
                    "you pass in instead"
                )
            self._owns_http = False
            self._http = http
        else:
            if base_url is None and transport is None:
                raise ConfigurationError(
                    "Client requires base_url= (optionally with transport= "
                    "and headers=), or a caller-owned http="
                )
            self._owns_http = True
            self._http = httpx.AsyncClient(
                base_url=base_url or "http://testserver",
                transport=transport,
                headers=dict(headers) if headers else None,
            )
        self._errors = declared_errors

    async def aclose(self) -> None:
        if self._owns_http:
            await self._http.aclose()

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        await self.aclose()

    async def call(
        self,
        contract: Contract[ResponseT, ResponseHeadersT],
        *,
        params: Any = None,
        query: Any = None,
        headers: Any = None,
        request: Any = _UNSET,
    ) -> ResponseT:
        """Send one request described by ``contract`` and return the
        validated response value.

        ``params`` and ``request`` are required when the contract declares
        them; explicitly passing ``request=None`` sends JSON ``null`` when
        the request type accepts it. ``query`` and ``headers`` may be omitted
        to rely on their models' defaults. Header field names and Pydantic
        aliases are sent with underscores replaced by hyphens
        (``x_api_key`` → ``x-api-key``).
        """
        response = await self.call_with_response(
            contract,
            params=params,
            query=query,
            headers=headers,
            request=request,
        )
        return response.body

    async def call_with_response(
        self,
        contract: Contract[ResponseT, ResponseHeadersT],
        *,
        params: Any = None,
        query: Any = None,
        headers: Any = None,
        request: Any = _UNSET,
    ) -> ClientResponse[ResponseT, ResponseHeadersT]:
        """Send one contract call and return its validated body, declared
        success headers, and underlying :class:`httpx.Response`.

        Use :meth:`call` when only the response body matters. Both methods
        validate declared success headers; this method preserves them for the
        caller.
        """
        self._reject_undeclared(contract, params, query, headers, request)
        declared_errors = _validated_error_defs(
            (*contract.errors, *self._errors),
            label=f"Client.call_with_response({contract.name!r}) errors",
        )
        response_adapter, response_headers_adapter = _preflight_contract_types(contract)
        url = self._build_path(contract, params)
        query_values = self._build_query(contract, query)
        content, content_headers = self._build_body(contract, request)
        header_values = self._build_headers(contract, headers, content_headers)

        response = await self._http.request(
            contract.method,
            url,
            params=query_values,
            content=content,
            headers=header_values,
        )

        if response.status_code == contract.status:
            if contract.response is None:
                body = cast(ResponseT, None)
            else:
                assert response_adapter is not None
                if _is_json_media_type(contract.response_media_type):
                    body = response_adapter.validate_json(response.content)
                elif _is_text_media_type(contract.response_media_type):
                    # Charset-aware: httpx decodes per the response headers,
                    # so a latin-1 text body validates instead of failing a
                    # strict UTF-8 decode of the raw bytes.
                    body = response_adapter.validate_python(response.text)
                else:
                    body = response_adapter.validate_python(response.content)
            if response_headers_adapter is None:
                validated_headers = cast(ResponseHeadersT, None)
            else:
                schema = response_headers_adapter.json_schema(
                    mode="serialization", by_alias=True
                )
                fields = _response_header_fields(
                    schema,
                    label=f"{contract.name}: response_headers",
                )
                header_values = {
                    raw_name: response.headers.get_list(wire_name)[-1]
                    for raw_name, wire_name, _, _ in fields
                    if response.headers.get_list(wire_name)
                }
                validated_headers = response_headers_adapter.validate_python(
                    header_values
                )
            return ClientResponse(
                body=body,
                headers=validated_headers,
                http_response=response,
            )

        return self._raise_for_error(contract, response, declared_errors)

    @staticmethod
    def _reject_undeclared(
        contract: Contract[Any, Any],
        params: Any,
        query: Any,
        headers: Any,
        request: Any,
    ) -> None:
        """Supplying an input the contract has no slot for is a wiring
        mistake; dropping it silently would hide contract drift."""
        supplied = {
            "params": (params, contract.params),
            "query": (query, contract.query),
            "headers": (headers, contract.headers),
            "request": (request, contract.request),
        }
        for name, (value, declared) in supplied.items():
            omitted = value is _UNSET if name == "request" else value is None
            if not omitted and declared is None:
                raise TypeError(
                    f"{contract.name} does not declare {name}=; the value "
                    "passed to call() would be silently dropped"
                )

    def _build_path(self, contract: Contract[Any, Any], params: Any) -> str:
        if contract.params is None:
            return contract.path
        if params is None:
            raise TypeError(
                f"{contract.name} declares params={_type_name(contract.params)}; "
                "pass params= to call()"
            )
        adapter = _adapter(contract.params, contract=contract, slot="params")
        values = adapter.dump_python(
            adapter.validate_python(params), mode="json", by_alias=True
        )
        encoded: dict[str, str] = {}
        for key, value in values.items():
            if value is None or str(value) == "":
                raise ValueError(
                    f"{contract.name}: path parameter {key!r} must be a "
                    f"non-empty value, got {value!r}"
                )
            encoded[key] = quote(str(value), safe="")
        url = _PATH_PARAMETER.sub(
            lambda match: encoded.get(match.group(1), match.group(0)), contract.path
        )
        unfilled = [match.group(1) for match in _PATH_PARAMETER.finditer(url)]
        if unfilled:
            raise ValueError(
                f"{contract.name}: params left path segments unfilled: {unfilled}"
            )
        return url

    def _build_query(
        self, contract: Contract[Any, Any], query: Any
    ) -> dict[str, Any] | None:
        if contract.query is None or query is None:
            return None
        adapter = _adapter(contract.query, contract=contract, slot="query")
        values = adapter.dump_python(
            adapter.validate_python(query),
            mode="json",
            by_alias=True,
            exclude_none=True,
        )
        return cast(dict[str, Any], values)

    def _build_body(
        self, contract: Contract[Any, Any], request: Any
    ) -> tuple[bytes | None, dict[str, str] | None]:
        if contract.request is None:
            return None, None
        if request is _UNSET:
            raise TypeError(
                f"{contract.name} declares a request type; pass request= to call()"
            )
        adapter = _adapter(contract.request, contract=contract, slot="request")
        validated = adapter.validate_python(request)
        if _is_json_media_type(contract.request_media_type):
            content: bytes = adapter.dump_json(validated, by_alias=True)
        elif isinstance(validated, bytes):
            content = validated
        elif isinstance(validated, str):
            content = validated.encode("utf-8")
        else:
            # Silently sending JSON labeled as another media type would
            # produce a body the server rejects with no hint why.
            raise TypeError(
                f"{contract.name}: cannot encode "
                f"{type(validated).__name__} as "
                f"{contract.request_media_type}; non-JSON request media "
                "types require str or bytes request types"
            )
        return content, {"content-type": contract.request_media_type}

    def _build_headers(
        self,
        contract: Contract[Any, Any],
        headers: Any,
        content_headers: dict[str, str] | None,
    ) -> dict[str, str] | None:
        merged = dict(content_headers or {})
        if contract.headers is not None and headers is not None:
            adapter = _adapter(contract.headers, contract=contract, slot="headers")
            values = cast(
                dict[str, Any],
                adapter.dump_python(
                    adapter.validate_python(headers),
                    mode="json",
                    by_alias=True,
                    exclude_none=True,
                ),
            )
            for field, value in values.items():
                merged[field.replace("_", "-")] = str(value)
        return merged or None

    def _raise_for_error(
        self,
        contract: Contract[Any, Any],
        response: httpx.Response,
        errors: Sequence[ErrorDef],
    ) -> Any:
        try:
            body: Any = response.json()
        except ValueError:
            body = response.text

        if isinstance(body, dict):
            envelope = cast(dict[str, Any], body)
            code = envelope.get("code")
            message = envelope.get("message")
            if not isinstance(message, str):
                raise UnexpectedResponseError(
                    contract_name=contract.name,
                    status_code=response.status_code,
                    body=body,
                )
            for definition in errors:
                if (
                    definition.code == code
                    and definition.status == response.status_code
                    and response.headers.get(_ERROR_SOURCE_HEADER) == "app"
                ):
                    raise AppError(
                        definition,
                        message=message,
                        details=envelope.get("details"),
                        # Mirror the error contract's one header channel:
                        # the values the definition says this error carries.
                        headers={
                            name: response.headers[name]
                            for name in definition.headers
                            if name in response.headers
                        },
                    )

        raise UnexpectedResponseError(
            contract_name=contract.name,
            status_code=response.status_code,
            body=body,
        )


def _adapter(
    annotation: Any, *, contract: Contract[Any, Any], slot: str
) -> TypeAdapter[Any]:
    try:
        adapter = _adapters.get(annotation)
    except TypeError:  # unhashable annotation: skip the cache
        return _build_adapter(annotation, contract=contract, slot=slot)
    if adapter is None:
        adapter = _build_adapter(annotation, contract=contract, slot=slot)
        _adapters[annotation] = adapter
    return adapter


def _preflight_contract_types(
    contract: Contract[Any, Any],
) -> tuple[TypeAdapter[Any] | None, TypeAdapter[Any] | None]:
    """Build every declared adapter before a request can leave the process."""
    response_adapter: TypeAdapter[Any] | None = None
    response_headers_adapter: TypeAdapter[Any] | None = None
    for slot in (
        "params",
        "query",
        "headers",
        "request",
        "response",
        "response_headers",
    ):
        annotation = getattr(contract, slot)
        if annotation is None:
            continue
        adapter = _adapter(annotation, contract=contract, slot=slot)
        if slot in {"params", "query", "headers", "response_headers"}:
            try:
                mode = "serialization" if slot == "response_headers" else "validation"
                schema = adapter.json_schema(mode=mode, by_alias=True)
                validation_schema = (
                    adapter.json_schema(mode="validation", by_alias=True)
                    if slot == "response_headers"
                    else None
                )
            except Exception as exc:
                raise ConfigurationError(
                    f"{contract.name}: Pydantic cannot describe {slot} type "
                    f"{_type_name(annotation)}: {exc}"
                ) from exc
            if slot == "response_headers":
                _response_header_fields(
                    schema,
                    label=(
                        f"{contract.name}: response_headers type "
                        f"{_type_name(annotation)}"
                    ),
                    validation_schema=validation_schema,
                )
            elif _object_schema(schema) is None:
                raise ConfigurationError(
                    f"{contract.name}: {slot} type {_type_name(annotation)} must "
                    "describe object-shaped input"
                )
        if slot == "response":
            response_adapter = adapter
        elif slot == "response_headers":
            response_headers_adapter = adapter
    return response_adapter, response_headers_adapter


def _build_adapter(
    annotation: Any, *, contract: Contract[Any, Any], slot: str
) -> TypeAdapter[Any]:
    try:
        adapter = TypeAdapter(annotation)
        if not adapter.pydantic_complete:
            adapter.rebuild(raise_errors=True)
        if not adapter.pydantic_complete:
            raise TypeError("adapter remains incomplete after rebuilding")
        return adapter
    except Exception as exc:
        type_name = getattr(annotation, "__name__", repr(annotation))
        raise ConfigurationError(
            f"{contract.name}: Pydantic cannot build an adapter for {slot} "
            f"type {type_name}: {exc}"
        ) from exc


def _type_name(annotation: Any) -> str:
    return getattr(annotation, "__name__", repr(annotation))
