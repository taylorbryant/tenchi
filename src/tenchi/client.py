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
from types import TracebackType
from typing import Any, Self, cast
from urllib.parse import quote

import httpx
from pydantic import TypeAdapter

from .contracts import Contract, ResponseT
from .errors import AppError

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

    Construct with either ``base_url`` (the client owns an internal
    ``httpx.AsyncClient`` and closes it on ``aclose``) or ``http`` (bring
    your own configured client — for example one using
    ``httpx.ASGITransport`` in tests — which the caller keeps ownership of).
    """

    def __init__(
        self,
        *,
        base_url: str | None = None,
        http: httpx.AsyncClient | None = None,
    ) -> None:
        if (base_url is None) == (http is None):
            raise ValueError("Client requires exactly one of base_url= or http=")
        self._owns_http = http is None
        self._http = http or httpx.AsyncClient(base_url=base_url or "")

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
        request: Any = None,
    ) -> ResponseT:
        """Send one request described by ``contract`` and return the
        validated response value.

        ``params`` and ``request`` are required when the contract declares
        them; ``query`` may be omitted to rely on the query model's
        defaults.
        """
        url = self._build_path(contract, params)
        query_values = self._build_query(contract, query)
        content, headers = self._build_body(contract, request)

        response = await self._http.request(
            contract.method,
            url,
            params=query_values,
            content=content,
            headers=headers,
        )

        if response.status_code == contract.status:
            if contract.response is None:
                return cast(ResponseT, None)
            return _adapter(contract.response).validate_json(response.content)

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
        content = adapter.dump_json(adapter.validate_python(request))
        return content, {"content-type": "application/json"}

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
            for definition in contract.errors:
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
