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

import re
from collections.abc import Mapping, Sequence
from types import TracebackType
from typing import Any, Self, cast
from urllib.parse import quote

import httpx
from pydantic import TypeAdapter

from .contracts import Contract, ResponseT
from .errors import AppError, ErrorDef

_PLACEHOLDER = re.compile(r"\{(\w+)\}")

_adapters: dict[Any, TypeAdapter[Any]] = {}


class UnexpectedResponseError(Exception):
    """A response that matched neither the contract's success status nor a
    declared error."""

    def __init__(self, *, contract_name: str, status_code: int, body: Any) -> None:
        super().__init__(f"{contract_name} returned unexpected status {status_code}")
        self.contract_name = contract_name
        self.status_code = status_code
        self.body = body


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
        if http is not None:
            if base_url is not None or headers is not None or transport is not None:
                raise ValueError(
                    "Client: http= is mutually exclusive with base_url=, "
                    "headers=, and transport=; configure the httpx client "
                    "you pass in instead"
                )
            self._owns_http = False
            self._http = http
        else:
            if base_url is None and transport is None:
                raise ValueError(
                    "Client requires base_url= (optionally with transport= "
                    "and headers=), or a caller-owned http="
                )
            self._owns_http = True
            self._http = httpx.AsyncClient(
                base_url=base_url or "http://testserver",
                transport=transport,
                headers=dict(headers) if headers else None,
            )
        self._errors = tuple(errors)

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
        contract: Contract[ResponseT],
        *,
        params: Any = None,
        query: Any = None,
        headers: Any = None,
        request: Any = None,
    ) -> ResponseT:
        """Send one request described by ``contract`` and return the
        validated response value.

        ``params`` and ``request`` are required when the contract declares
        them; ``query`` and ``headers`` may be omitted to rely on their
        models' defaults. Header field names are sent with underscores
        replaced by hyphens (``x_api_key`` → ``x-api-key``).
        """
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
                return cast(ResponseT, None)
            adapter = _adapter(contract.response)
            if contract.response_media_type == "application/json":
                return adapter.validate_json(response.content)
            return adapter.validate_python(response.content)

        return self._raise_for_error(contract, response)

    def _build_path(self, contract: Contract[Any], params: Any) -> str:
        if contract.params is None:
            return contract.path
        if params is None:
            raise TypeError(
                f"{contract.name} declares params={contract.params.__name__}; "
                "pass params= to call()"
            )
        adapter = _adapter(contract.params)
        values = adapter.dump_python(adapter.validate_python(params), mode="json")
        url = contract.path
        for key, value in values.items():
            url = url.replace("{" + key + "}", quote(str(value), safe=""))
        unfilled = _PLACEHOLDER.findall(url)
        if unfilled:
            raise ValueError(
                f"{contract.name}: params left path segments unfilled: {unfilled}"
            )
        return url

    def _build_query(
        self, contract: Contract[Any], query: Any
    ) -> dict[str, Any] | None:
        if contract.query is None or query is None:
            return None
        adapter = _adapter(contract.query)
        values = adapter.dump_python(
            adapter.validate_python(query), mode="json", exclude_none=True
        )
        return cast(dict[str, Any], values)

    def _build_body(
        self, contract: Contract[Any], request: Any
    ) -> tuple[bytes | None, dict[str, str] | None]:
        if contract.request is None:
            return None, None
        if request is None:
            raise TypeError(
                f"{contract.name} declares a request type; pass request= to call()"
            )
        adapter = _adapter(contract.request)
        validated = adapter.validate_python(request)
        if contract.request_media_type == "application/json":
            content: bytes = adapter.dump_json(validated)
        elif isinstance(validated, bytes):
            content = validated
        elif isinstance(validated, str):
            content = validated.encode("utf-8")
        else:
            content = adapter.dump_json(validated)
        return content, {"content-type": contract.request_media_type}

    def _build_headers(
        self,
        contract: Contract[Any],
        headers: Any,
        content_headers: dict[str, str] | None,
    ) -> dict[str, str] | None:
        merged = dict(content_headers or {})
        if contract.headers is not None and headers is not None:
            adapter = _adapter(contract.headers)
            values = cast(
                dict[str, Any],
                adapter.dump_python(
                    adapter.validate_python(headers), mode="json", exclude_none=True
                ),
            )
            for field, value in values.items():
                merged[field.replace("_", "-")] = str(value)
        return merged or None

    def _raise_for_error(
        self, contract: Contract[Any], response: httpx.Response
    ) -> Any:
        try:
            body: Any = response.json()
        except ValueError:
            body = response.text

        if isinstance(body, dict):
            envelope = cast(dict[str, Any], body)
            code = envelope.get("code")
            for definition in (*contract.errors, *self._errors):
                if (
                    definition.code == code
                    and definition.status == response.status_code
                ):
                    raise AppError(
                        definition,
                        message=envelope.get("message"),
                        details=envelope.get("details"),
                    )

        raise UnexpectedResponseError(
            contract_name=contract.name,
            status_code=response.status_code,
            body=body,
        )


def _adapter(annotation: Any) -> TypeAdapter[Any]:
    adapter = _adapters.get(annotation)
    if adapter is None:
        adapter = TypeAdapter(annotation)
        _adapters[annotation] = adapter
    return adapter
