"""Typed HTTP client driven by the same contracts as the server.

:class:`Client` sends a request described by a contract and returns the
validated response type, so callers get real static types from the single
source of truth. For declared response variants,
``ClientResponse.definition`` identifies the status-specific declaration::

    async with Client(base_url="http://localhost:8000") as client:
        todo = await client.call(create_todo_contract, request=CreateTodo(...))

Error semantics mirror the server: an error response whose code and status
match one of the contract's declared errors is raised as :class:`AppError`
carrying that definition; anything else raises
:class:`UnexpectedResponseError`. Optional observers receive immutable,
payload-safe outcomes for metrics, logs, and tracing.
"""

from __future__ import annotations

import asyncio
import inspect
import logging
import math
import random
from asyncio import CancelledError
from collections.abc import Callable, Mapping, Sequence
from contextlib import suppress
from copy import deepcopy
from dataclasses import dataclass, field
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from time import perf_counter
from types import TracebackType
from typing import Any, Literal, Self, cast
from urllib.parse import quote

import httpx
from pydantic import TypeAdapter

from ._media_types import (
    MediaTypeError,
    is_json_media_type,
    is_text_media_type,
    media_type_matches,
    text_charset,
)
from ._values import same_python_value
from .contracts import (
    _PATH_PARAMETER,  # pyright: ignore[reportPrivateUsage]
    Contract,
    ResponseHeadersT,
    ResponseT,
    _object_schema,  # pyright: ignore[reportPrivateUsage]
    _query_parameter_is_sequence,  # pyright: ignore[reportPrivateUsage]
    _request_parameter_fields,  # pyright: ignore[reportPrivateUsage]
    _response_header_fields,  # pyright: ignore[reportPrivateUsage]
    _validate_idempotency_key_header,  # pyright: ignore[reportPrivateUsage]
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
from .responses import ResponseDef
from .retries import RetryPolicy, RetryTimeoutError

_adapters: dict[Any, TypeAdapter[Any]] = {}

logger = logging.getLogger("tenchi.client")


class _RetryDeadlineElapsed(TimeoutError):
    """Internal marker for policy expiry rather than a user TimeoutError."""


class _Unset:
    def __repr__(self) -> str:
        return "UNSET"


_UNSET = _Unset()
_INVALID_RESPONSE_VALUE = object()


class UnexpectedResponseError(TenchiError):
    """A response that did not satisfy a declared success or error outcome."""

    def __init__(
        self,
        *,
        contract_name: str,
        status_code: int,
        reason: str | None = None,
    ) -> None:
        message = (
            f"{contract_name} returned unexpected status {status_code}"
            if reason is None
            else f"{contract_name} returned an invalid response: {reason}"
        )
        super().__init__(message)
        self.contract_name = contract_name
        self.status_code = status_code
        self.reason = reason


@dataclass(frozen=True, slots=True)
class ClientOutcome:
    """The payload-safe result of one contract-driven client call.

    ``duration_seconds`` spans local input preparation, transport I/O, and
    response validation. Observer work is excluded. Bodies, headers, URLs,
    input values, and exception objects are deliberately absent.
    ``completed_at`` is captured before logical-call observers run.
    """

    contract: Contract[Any, Any]
    status: Literal[
        "succeeded",
        "app_error",
        "unexpected_response",
        "transport_error",
        "timed_out",
        "failed",
        "cancelled",
    ]
    status_code: int | None
    duration_seconds: float
    error_code: str | None = None
    attempts: int = 1
    completed_at: datetime = field(default_factory=lambda: datetime.now(UTC))


ClientObserver = Callable[[ClientOutcome], Any]
"""A sync or async observer of finalized outbound client outcomes.

Observers run in declaration order before the call returns or raises. Their
failures are logged and isolated from later observers and the caller.
"""


@dataclass(frozen=True, slots=True)
class ClientAttemptOutcome:
    """The payload-safe result of one transport attempt.

    ``will_retry`` describes the decision made after this attempt.
    ``retry_delay_seconds`` is the selected backoff, including a valid
    ``Retry-After`` response header from a selected status or declared error
    when present and bounded by the retry policy's maximum delay.
    ``completed_at`` is captured before attempt observers run.
    """

    contract: Contract[Any, Any]
    attempt: int
    max_attempts: int
    status: Literal[
        "succeeded",
        "app_error",
        "unexpected_response",
        "transport_error",
        "timed_out",
        "failed",
        "cancelled",
    ]
    status_code: int | None
    duration_seconds: float
    error_code: str | None = None
    will_retry: bool = False
    retry_delay_seconds: float | None = None
    completed_at: datetime = field(default_factory=lambda: datetime.now(UTC))


ClientAttemptObserver = Callable[[ClientAttemptOutcome], Any]
"""A sync or async observer of each finalized outbound attempt."""


@dataclass(frozen=True, slots=True)
class ClientResponse[BodyT, HeadersT]:
    """A validated response body together with its declared headers and
    underlying httpx response."""

    body: BodyT
    headers: HeadersT
    http_response: httpx.Response
    definition: ResponseDef[Any, Any] | None = None
    """Selected definition, or ``None`` for a singular-response contract."""


@dataclass(frozen=True, slots=True)
class _ClientResponseDef:
    definition: ResponseDef[Any, Any]
    response_adapter: TypeAdapter[Any] | None
    response_headers_adapter: TypeAdapter[Any] | None


class _ClientAttempt:
    """Track one attempt so failures can be classified without payloads."""

    __slots__ = (
        "attempt",
        "contract",
        "max_attempts",
        "outcome",
        "remote_error_code",
        "retry_after_header",
        "started",
        "status_code",
    )

    def __init__(
        self,
        contract: Contract[Any, Any],
        *,
        attempt: int,
        max_attempts: int,
    ) -> None:
        self.contract = contract
        self.attempt = attempt
        self.max_attempts = max_attempts
        self.started = perf_counter()
        self.status_code: int | None = None
        self.remote_error_code: str | None = None
        self.retry_after_header: str | None = None
        self.outcome: ClientAttemptOutcome | None = None

    def response_received(self, response: httpx.Response) -> None:
        self.status_code = response.status_code
        self.retry_after_header = response.headers.get("retry-after")

    def remote_app_error(self, error: AppError) -> None:
        self.remote_error_code = error.code

    def finish(
        self,
        status: Literal[
            "succeeded",
            "app_error",
            "unexpected_response",
            "transport_error",
            "timed_out",
            "failed",
            "cancelled",
        ],
        *,
        error_code: str | None = None,
        will_retry: bool = False,
        retry_delay_seconds: float | None = None,
    ) -> ClientAttemptOutcome:
        outcome = ClientAttemptOutcome(
            contract=self.contract,
            attempt=self.attempt,
            max_attempts=self.max_attempts,
            status=status,
            status_code=self.status_code,
            duration_seconds=perf_counter() - self.started,
            error_code=error_code,
            will_retry=will_retry,
            retry_delay_seconds=retry_delay_seconds,
            completed_at=datetime.now(UTC),
        )
        self.outcome = outcome
        return outcome


class _ClientCall:
    """Track one logical client call across all transport attempts."""

    __slots__ = (
        "active_attempt",
        "attempts",
        "contract",
        "observer_seconds",
        "outcome",
        "started",
        "status_code",
    )

    def __init__(self, contract: Contract[Any, Any]) -> None:
        self.contract = contract
        self.started = perf_counter()
        self.status_code: int | None = None
        self.attempts = 0
        self.observer_seconds = 0.0
        self.active_attempt: _ClientAttempt | None = None
        self.outcome: ClientOutcome | None = None

    def record(self, attempt: _ClientAttempt) -> None:
        self.attempts = max(self.attempts, attempt.attempt)
        self.status_code = attempt.status_code
        self.active_attempt = None

    def finish(
        self,
        status: Literal[
            "succeeded",
            "app_error",
            "unexpected_response",
            "transport_error",
            "timed_out",
            "failed",
            "cancelled",
        ],
        *,
        error_code: str | None = None,
        completed_at: datetime | None = None,
    ) -> None:
        self.outcome = ClientOutcome(
            contract=self.contract,
            status=status,
            status_code=self.status_code,
            duration_seconds=max(
                0.0,
                perf_counter() - self.started - self.observer_seconds,
            ),
            error_code=error_code,
            attempts=self.attempts,
            completed_at=completed_at or datetime.now(UTC),
        )


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

    ``observers`` receive one immutable, payload-safe :class:`ClientOutcome`
    for every call. Sync and async observers run in order; failures are logged
    and do not alter the returned value or raised exception.

    ``attempt_observers`` receive one :class:`ClientAttemptOutcome` for each
    transport attempt. Calls still make exactly one attempt unless a
    :class:`~tenchi.retries.RetryPolicy` is passed explicitly.
    """

    def __init__(
        self,
        *,
        base_url: str | None = None,
        headers: Mapping[str, str] | None = None,
        transport: httpx.AsyncBaseTransport | None = None,
        http: httpx.AsyncClient | None = None,
        errors: Sequence[ErrorDef] = (),
        observers: Sequence[ClientObserver] = (),
        attempt_observers: Sequence[ClientAttemptObserver] = (),
    ) -> None:
        declared_errors = _validated_error_defs(errors, label="Client errors")
        observer_chain = _validated_observers(
            observers,
            label="observers",
            argument="ClientOutcome",
        )
        attempt_observer_chain = _validated_observers(
            attempt_observers,
            label="attempt_observers",
            argument="ClientAttemptOutcome",
        )
        if http is not None:
            if base_url is not None or headers is not None or transport is not None:
                raise ConfigurationError(
                    "Client: http= is mutually exclusive with base_url=, "
                    "headers=, and transport=; configure the httpx client "
                    "you pass in instead"
                )
            self._owns_http = False
            self._http = http
            self._configured_headers = http.headers
        else:
            if base_url is None and transport is None:
                raise ConfigurationError(
                    "Client requires base_url= (optionally with transport= "
                    "and headers=), or a caller-owned http="
                )
            self._owns_http = True
            configured_headers = dict(headers) if headers else {}
            self._http = httpx.AsyncClient(
                base_url=base_url or "http://testserver",
                transport=transport,
                headers=configured_headers or None,
            )
            self._configured_headers = configured_headers
        self._errors = declared_errors
        self._observers = observer_chain
        self._attempt_observers = attempt_observer_chain

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
        retry: RetryPolicy | None = None,
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
            retry=retry,
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
        retry: RetryPolicy | None = None,
    ) -> ClientResponse[ResponseT, ResponseHeadersT]:
        """Send one contract call and return its validated body, declared
        success headers, and underlying :class:`httpx.Response`.

        Use :meth:`call` when only the response body matters. Both methods
        validate declared success headers; this method preserves them for the
        caller.
        """
        policy = self._validated_retry_policy(contract, retry)
        if policy is None and not self._observers and not self._attempt_observers:
            return await self._perform_call(
                contract,
                params=params,
                query=query,
                headers=headers,
                request=request,
            )

        call = _ClientCall(contract)
        try:
            deadline = (
                asyncio.get_running_loop().time() + policy.total_timeout_seconds
                if policy is not None and policy.total_timeout_seconds is not None
                else None
            )
            return await self._run_attempts(
                contract,
                params=params,
                query=query,
                headers=headers,
                request=request,
                policy=policy,
                call=call,
                deadline=deadline,
            )
        except _RetryDeadlineElapsed as exc:
            if policy is None or policy.total_timeout_seconds is None:
                raise
            if call.active_attempt is not None:
                attempt_outcome = call.active_attempt.finish("timed_out")
                await self._finish_attempt(call, call.active_attempt)
                call.finish(
                    "timed_out",
                    completed_at=attempt_outcome.completed_at,
                )
            else:
                call.finish("timed_out")
            raise RetryTimeoutError(
                contract_name=contract.name,
                attempts=call.attempts,
            ) from exc
        except CancelledError:
            if call.active_attempt is not None:
                attempt_outcome = call.active_attempt.finish("cancelled")
                await self._finish_attempt(call, call.active_attempt)
                call.finish(
                    "cancelled",
                    completed_at=attempt_outcome.completed_at,
                )
            else:
                call.finish("cancelled")
            raise
        finally:
            await _notify_client_observers(self._observers, call)

    async def _run_attempts(
        self,
        contract: Contract[ResponseT, ResponseHeadersT],
        *,
        params: Any,
        query: Any,
        headers: Any,
        request: Any,
        policy: RetryPolicy | None,
        call: _ClientCall,
        deadline: float | None,
    ) -> ClientResponse[ResponseT, ResponseHeadersT]:
        max_attempts = policy.max_attempts if policy is not None else 1
        for attempt_number in range(1, max_attempts + 1):
            attempt = _ClientAttempt(
                contract,
                attempt=attempt_number,
                max_attempts=max_attempts,
            )
            call.active_attempt = attempt
            deadline_scope = asyncio.timeout_at(deadline)
            try:
                async with deadline_scope:
                    result = await self._perform_call(
                        contract,
                        params=params,
                        query=query,
                        headers=headers,
                        request=request,
                        call=attempt,
                    )
            except CancelledError:
                raise
            except AppError as exc:
                error_code = attempt.remote_error_code
                retry_by_code = (
                    policy is not None
                    and error_code is not None
                    and error_code in policy.retry_on
                )
                retry_by_status = (
                    policy is not None
                    and attempt.status_code is not None
                    and attempt.status_code in policy.retry_on_statuses
                )
                should_retry = (
                    retry_by_code or retry_by_status
                ) and attempt_number < max_attempts
                delay = (
                    _retry_delay(
                        policy,
                        attempt_number,
                        retry_after=(
                            attempt.retry_after_header
                            if retry_by_status
                            else _app_error_retry_after(exc)
                        ),
                    )
                    if should_retry and policy is not None
                    else None
                )
                app_status: Literal["app_error", "unexpected_response", "failed"]
                if error_code is not None:
                    app_status = "app_error"
                elif attempt.status_code is not None:
                    app_status = "unexpected_response"
                else:
                    app_status = "failed"
                attempt_outcome = attempt.finish(
                    app_status,
                    error_code=error_code,
                    will_retry=should_retry,
                    retry_delay_seconds=delay,
                )
                observer_seconds = await self._finish_attempt(call, attempt)
                if deadline is not None:
                    deadline += observer_seconds
                if not should_retry:
                    call.finish(
                        app_status,
                        error_code=error_code,
                        completed_at=attempt_outcome.completed_at,
                    )
                    raise
            except UnexpectedResponseError:
                should_retry = (
                    policy is not None
                    and attempt.status_code is not None
                    and attempt.status_code in policy.retry_on_statuses
                    and attempt_number < max_attempts
                )
                delay = (
                    _retry_delay(
                        policy,
                        attempt_number,
                        retry_after=attempt.retry_after_header,
                    )
                    if should_retry and policy is not None
                    else None
                )
                attempt_outcome = attempt.finish(
                    "unexpected_response",
                    will_retry=should_retry,
                    retry_delay_seconds=delay,
                )
                observer_seconds = await self._finish_attempt(call, attempt)
                if deadline is not None:
                    deadline += observer_seconds
                if not should_retry:
                    call.finish(
                        "unexpected_response",
                        completed_at=attempt_outcome.completed_at,
                    )
                    raise
            except httpx.TransportError:
                transport_status: Literal["transport_error", "unexpected_response"] = (
                    "transport_error"
                    if attempt.status_code is None
                    else "unexpected_response"
                )
                should_retry = (
                    policy is not None
                    and policy.retry_transport_errors
                    and transport_status == "transport_error"
                    and attempt_number < max_attempts
                )
                delay = (
                    _retry_delay(policy, attempt_number)
                    if should_retry and policy is not None
                    else None
                )
                attempt_outcome = attempt.finish(
                    transport_status,
                    will_retry=should_retry,
                    retry_delay_seconds=delay,
                )
                observer_seconds = await self._finish_attempt(call, attempt)
                if deadline is not None:
                    deadline += observer_seconds
                if not should_retry:
                    call.finish(
                        transport_status,
                        completed_at=attempt_outcome.completed_at,
                    )
                    raise
            except TimeoutError as exc:
                if deadline_scope.expired():
                    raise _RetryDeadlineElapsed from exc
                failure_status: Literal["unexpected_response", "failed"] = (
                    "unexpected_response"
                    if attempt.status_code is not None
                    else "failed"
                )
                attempt_outcome = attempt.finish(failure_status)
                await self._finish_attempt(call, attempt)
                call.finish(
                    failure_status,
                    completed_at=attempt_outcome.completed_at,
                )
                raise
            except BaseException:
                failure_status: Literal["unexpected_response", "failed"] = (
                    "unexpected_response"
                    if attempt.status_code is not None
                    else "failed"
                )
                attempt_outcome = attempt.finish(failure_status)
                await self._finish_attempt(call, attempt)
                call.finish(
                    failure_status,
                    completed_at=attempt_outcome.completed_at,
                )
                raise
            else:
                attempt_outcome = attempt.finish("succeeded")
                await self._finish_attempt(call, attempt)
                call.finish(
                    "succeeded",
                    completed_at=attempt_outcome.completed_at,
                )
                return result

            assert delay is not None
            sleep_scope = asyncio.timeout_at(deadline)
            try:
                async with sleep_scope:
                    await asyncio.sleep(delay)
            except TimeoutError as exc:
                if sleep_scope.expired():
                    raise _RetryDeadlineElapsed from exc
                raise
        raise AssertionError("client retry loop exhausted without a result")

    async def _finish_attempt(
        self,
        call: _ClientCall,
        attempt: _ClientAttempt,
    ) -> float:
        call.record(attempt)
        observer_seconds = await _notify_client_attempt_observers(
            self._attempt_observers,
            attempt,
        )
        call.observer_seconds += observer_seconds
        return observer_seconds

    def _validated_retry_policy(
        self,
        contract: Contract[Any, Any],
        value: object,
    ) -> RetryPolicy | None:
        if value is None:
            return None
        if not isinstance(value, RetryPolicy):
            raise ConfigurationError(
                f"{contract.name}: retry must be a RetryPolicy or None"
            )
        policy = value
        if (
            contract.method not in {"GET", "HEAD", "OPTIONS", "TRACE"}
            and not policy.allow_unsafe_methods
        ):
            raise ConfigurationError(
                f"{contract.name}: retrying {contract.method} requires "
                "allow_unsafe_methods=True; pair unsafe retries with an "
                "idempotency key"
            )
        declared = {error.code for error in (*contract.errors, *self._errors)}
        unknown = [code for code in policy.retry_on if code not in declared]
        if unknown:
            raise ConfigurationError(
                f"{contract.name}: retry_on contains undeclared error "
                f"code{'s' if len(unknown) != 1 else ''} {unknown!r}"
            )
        return policy

    async def _perform_call(
        self,
        contract: Contract[ResponseT, ResponseHeadersT],
        *,
        params: Any,
        query: Any,
        headers: Any,
        request: Any,
        call: _ClientAttempt | None = None,
    ) -> ClientResponse[ResponseT, ResponseHeadersT]:
        self._reject_undeclared(contract, params, query, headers, request)
        declared_errors = _validated_error_defs(
            (*contract.errors, *self._errors),
            label=f"Client.call_with_response({contract.name!r}) errors",
        )
        response_adapter, response_headers_adapter, responses = (
            _preflight_contract_types(contract)
        )
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
        if call is not None:
            call.response_received(response)

        selected = next(
            (
                declared
                for declared in responses
                if declared.definition.status == response.status_code
            ),
            None,
        )
        is_singular_response = not responses and response.status_code == contract.status
        if selected is not None or is_singular_response:
            response_type = (
                selected.definition.body if selected is not None else contract.response
            )
            response_media_type = (
                selected.definition.media_type
                if selected is not None
                else contract.response_media_type
            )
            selected_response_adapter = (
                selected.response_adapter if selected is not None else response_adapter
            )
            selected_headers_adapter = (
                selected.response_headers_adapter
                if selected is not None
                else response_headers_adapter
            )
            if response_type is None:
                if response.content:
                    raise UnexpectedResponseError(
                        contract_name=contract.name,
                        status_code=response.status_code,
                        reason="the declared response requires an empty body",
                    )
                body = cast(ResponseT, None)
            else:
                assert selected_response_adapter is not None
                assert response_media_type is not None
                self._require_response_media_type(
                    contract,
                    response,
                    declared=response_media_type,
                )
                validated_body: object = _INVALID_RESPONSE_VALUE
                try:
                    if is_json_media_type(response_media_type):
                        validated_body = selected_response_adapter.validate_json(
                            response.content
                        )
                    elif is_text_media_type(response_media_type):
                        validated_body = selected_response_adapter.validate_python(
                            self._decode_text_response(contract, response)
                        )
                    else:
                        validated_body = selected_response_adapter.validate_python(
                            response.content
                        )
                except UnexpectedResponseError:
                    raise
                except Exception:
                    pass
                if validated_body is _INVALID_RESPONSE_VALUE:
                    raise UnexpectedResponseError(
                        contract_name=contract.name,
                        status_code=response.status_code,
                        reason="response body does not match the declared schema",
                    )
                body = cast(ResponseT, validated_body)
            if selected_headers_adapter is None:
                validated_headers = cast(ResponseHeadersT, None)
            else:
                schema = selected_headers_adapter.json_schema(
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
                validated_header_value: object = _INVALID_RESPONSE_VALUE
                with suppress(Exception):
                    validated_header_value = selected_headers_adapter.validate_python(
                        header_values
                    )
                if validated_header_value is _INVALID_RESPONSE_VALUE:
                    raise UnexpectedResponseError(
                        contract_name=contract.name,
                        status_code=response.status_code,
                        reason="response headers do not match the declared schema",
                    )
                validated_headers = cast(ResponseHeadersT, validated_header_value)
            return ClientResponse(
                body=body,
                headers=validated_headers,
                http_response=response,
                definition=selected.definition if selected is not None else None,
            )

        return self._raise_for_error(
            contract,
            response,
            declared_errors,
            call=call,
        )

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
        validated = adapter.validate_python(params)
        serialization_value = _parameter_serialization_copy(
            validated,
            label=f"{contract.name}: params",
        )
        values = _serialized_parameter_values(
            adapter,
            adapter.dump_python(serialization_value, mode="json", by_alias=True),
            location="path",
            label=f"{contract.name}: params",
        )
        rendered: dict[str, str] = {}
        for key, value in values.items():
            if value is None or str(value) == "":
                raise ValueError(
                    f"{contract.name}: path parameter {key!r} must be a "
                    f"non-empty value, got {value!r}"
                )
            rendered[key] = str(value)
        _validate_parameter_round_trip(
            adapter,
            validated,
            rendered,
            label=f"{contract.name}: params",
        )
        encoded = {key: quote(value, safe="") for key, value in rendered.items()}
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
        if contract.query is None:
            return None
        adapter = _adapter(contract.query, contract=contract, slot="query")
        if query is None:
            adapter.validate_python(_encoded_query_input(adapter, self._http.params))
            return None
        validated = adapter.validate_python(query)
        serialization_value = _parameter_serialization_copy(
            validated,
            label=f"{contract.name}: query",
        )
        typed_values = _serialized_parameter_values(
            adapter,
            adapter.dump_python(
                serialization_value,
                mode="json",
                by_alias=True,
                exclude_none=True,
            ),
            location="query",
            label=f"{contract.name}: query",
        )
        effective = self._http.params.merge(typed_values)
        _validate_parameter_round_trip(
            adapter,
            validated,
            _encoded_query_input(adapter, effective),
            label=f"{contract.name}: query",
        )
        return typed_values

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
        if is_json_media_type(contract.request_media_type):
            content: bytes = adapter.dump_json(validated, by_alias=True)
        elif isinstance(validated, bytes):
            content = validated
        elif isinstance(validated, str):
            try:
                charset = (
                    text_charset(contract.request_media_type)
                    if is_text_media_type(contract.request_media_type)
                    else "utf-8"
                )
            except MediaTypeError as exc:
                raise ConfigurationError(
                    f"{contract.name}: invalid request media type: {exc}"
                ) from exc
            try:
                content = validated.encode(charset)
            except LookupError as exc:
                raise ConfigurationError(
                    f"{contract.name}: unsupported request charset {charset!r}"
                ) from exc
            except UnicodeEncodeError as exc:
                raise ValueError(
                    f"{contract.name}: request text cannot be encoded as {charset!r}"
                ) from exc
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
        if contract.headers is not None:
            adapter = _adapter(contract.headers, contract=contract, slot="headers")
            configured = dict(self._configured_headers)
            configured.update(merged)
            validated = adapter.validate_python(
                _encoded_header_input(adapter, configured)
                if headers is None
                else headers
            )
            serialization_value = _parameter_serialization_copy(
                validated,
                label=f"{contract.name}: headers",
            )
            values = _serialized_parameter_values(
                adapter,
                adapter.dump_python(
                    serialization_value,
                    mode="json",
                    by_alias=True,
                    exclude_none=True,
                ),
                location="header",
                label=f"{contract.name}: headers",
            )
            rendered = {
                field.replace("_", "-"): _render_request_header_value(
                    field.replace("_", "-"),
                    value,
                    label=f"{contract.name}: headers",
                )
                for field, value in values.items()
            }
            effective = dict(self._http.headers)
            effective.update(merged)
            effective.update(rendered)
            _validate_parameter_round_trip(
                adapter,
                validated,
                _encoded_header_input(adapter, effective),
                label=f"{contract.name}: headers",
            )
            merged.update(rendered)
        return merged or None

    def _raise_for_error(
        self,
        contract: Contract[Any, Any],
        response: httpx.Response,
        errors: Sequence[ErrorDef],
        *,
        call: _ClientAttempt | None,
    ) -> Any:
        if response.status_code >= 400:
            self._require_response_media_type(
                contract,
                response,
                declared="application/json",
            )
        invalid_json = False
        try:
            body: Any = response.json()
        except ValueError:
            invalid_json = True
            body = response.text
        if invalid_json and response.status_code >= 400:
            raise UnexpectedResponseError(
                contract_name=contract.name,
                status_code=response.status_code,
                reason="error response envelope is invalid",
            )

        if isinstance(body, dict):
            envelope = cast(dict[str, Any], body)
            code = envelope.get("code")
            message = envelope.get("message")
            if response.status_code >= 400 and (
                not isinstance(code, str) or not isinstance(message, str)
            ):
                raise UnexpectedResponseError(
                    contract_name=contract.name,
                    status_code=response.status_code,
                    reason="error response envelope is invalid",
                )
            for definition in errors:
                if (
                    definition.code == code
                    and definition.status == response.status_code
                    and response.headers.get(_ERROR_SOURCE_HEADER) == "app"
                ):
                    error = AppError(
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
                    if call is not None:
                        call.remote_app_error(error)
                    raise error
        elif response.status_code >= 400:
            raise UnexpectedResponseError(
                contract_name=contract.name,
                status_code=response.status_code,
                reason="error response envelope is invalid",
            )

        raise UnexpectedResponseError(
            contract_name=contract.name,
            status_code=response.status_code,
        )

    @staticmethod
    def _require_response_media_type(
        contract: Contract[Any, Any],
        response: httpx.Response,
        *,
        declared: str,
    ) -> None:
        actual = response.headers.get("content-type")
        if media_type_matches(declared=declared, actual=actual):
            return
        raise UnexpectedResponseError(
            contract_name=contract.name,
            status_code=response.status_code,
            reason=(
                f"response content type does not match declared media type {declared!r}"
            ),
        )

    @staticmethod
    def _decode_text_response(
        contract: Contract[Any, Any], response: httpx.Response
    ) -> str:
        actual = response.headers.get("content-type")
        assert actual is not None
        content_type_invalid = False
        try:
            charset = text_charset(actual)
        except MediaTypeError:
            content_type_invalid = True
            charset = ""
        if content_type_invalid:
            raise UnexpectedResponseError(
                contract_name=contract.name,
                status_code=response.status_code,
                reason="response content type is invalid",
            )
        charset_error: str | None = None
        decoded: str | None = None
        try:
            decoded = response.content.decode(charset)
        except LookupError:
            charset_error = "response declares an unsupported charset"
        except UnicodeDecodeError:
            charset_error = "response body is not valid for the declared charset"
        if charset_error is not None:
            raise UnexpectedResponseError(
                contract_name=contract.name,
                status_code=response.status_code,
                reason=charset_error,
            )
        assert decoded is not None
        return decoded


async def _notify_client_observers(
    observers: tuple[ClientObserver, ...],
    call: _ClientCall,
) -> None:
    outcome = call.outcome
    if outcome is None:
        return
    for index, observer in enumerate(observers):
        try:
            result = observer(outcome)
            if inspect.isawaitable(result):
                await result
        except Exception:
            logger.exception(
                "Client observer[%d] failed for %s",
                index,
                outcome.contract.name,
            )


async def _notify_client_attempt_observers(
    observers: tuple[ClientAttemptObserver, ...],
    attempt: _ClientAttempt,
) -> float:
    outcome = attempt.outcome
    if outcome is None:
        return 0.0
    started = perf_counter()
    for index, observer in enumerate(observers):
        try:
            result = observer(outcome)
            if inspect.isawaitable(result):
                await result
        except Exception:
            logger.exception(
                "Client attempt observer[%d] failed for %s attempt %d",
                index,
                outcome.contract.name,
                outcome.attempt,
            )
    return perf_counter() - started


def _validated_observers[OutcomeT](
    observers: Sequence[Callable[[OutcomeT], Any]],
    *,
    label: str,
    argument: str,
) -> tuple[Callable[[OutcomeT], Any], ...]:
    chain = tuple(observers)
    for index, observer in enumerate(chain):
        try:
            signature = inspect.signature(observer)
            signature.bind(object())
        except (TypeError, ValueError) as exc:
            raise ConfigurationError(
                f"Client: {label}[{index}] must accept one positional "
                f"{argument} argument: {exc}"
            ) from exc
    return chain


def _retry_delay(
    policy: RetryPolicy,
    attempt: int,
    *,
    retry_after: str | None = None,
) -> float:
    exponential = policy.base_delay_seconds * (2.0 ** min(attempt - 1, 1023))
    if exponential and policy.jitter:
        exponential *= random.uniform(1 - policy.jitter, 1 + policy.jitter)
    exponential = min(policy.max_delay_seconds, exponential)
    retry_after_seconds = (
        _retry_after_seconds(retry_after, maximum=policy.max_delay_seconds)
        if retry_after is not None
        else None
    )
    return min(
        policy.max_delay_seconds,
        max(exponential, retry_after_seconds or 0.0),
    )


def _app_error_retry_after(error: AppError) -> str | None:
    return next(
        (
            value
            for name, value in error.headers.items()
            if name.casefold() == "retry-after"
        ),
        None,
    )


def _retry_after_seconds(raw: str, *, maximum: float) -> float | None:
    if raw.isascii() and raw.isdigit():
        significant = raw.lstrip("0") or "0"
        # A finite binary64 value has at most 309 significant decimal digits.
        # Avoid handing an arbitrarily large untrusted integer to float().
        if len(significant) > 309:
            return maximum
        delay = float(significant)
        if not math.isfinite(delay):
            return maximum
    else:
        try:
            value = parsedate_to_datetime(raw)
        except (TypeError, ValueError, OverflowError):
            return None
        if value.tzinfo is None:
            value = value.replace(tzinfo=UTC)
        delay = (value - datetime.now(UTC)).total_seconds()
    if not delay >= 0 or not delay < float("inf"):
        return None
    return min(maximum, delay)


def _encoded_query_input(
    adapter: TypeAdapter[Any], values: Mapping[str, Any]
) -> dict[str, str | list[str]]:
    schema = adapter.json_schema(mode="validation", by_alias=True)
    sequence_fields = frozenset(
        name
        for name, field_schema in _request_parameter_fields(
            schema,
            location="query",
            label="query input",
        )
        if _query_parameter_is_sequence(schema, field_schema, seen_refs=set())
    )
    encoded: dict[str, str | list[str]] = {}
    for key, value in httpx.QueryParams(values).multi_items():
        existing = encoded.get(key)
        if existing is None:
            encoded[key] = [value] if key in sequence_fields else value
        elif isinstance(existing, list):
            existing.append(value)
        else:
            encoded[key] = [existing, value]
    return encoded


def _encoded_header_input(
    adapter: TypeAdapter[Any], values: Mapping[str, str]
) -> dict[str, str]:
    schema = adapter.json_schema(mode="validation", by_alias=True)
    fields = _request_parameter_fields(
        schema,
        location="header",
        label="header input",
    )
    normalized = {name.casefold(): value for name, value in values.items()}
    return {
        name: normalized[wire_name]
        for name, _ in fields
        if (wire_name := name.replace("_", "-").casefold()) in normalized
    }


def _render_request_header_value(name: str, value: object, *, label: str) -> str:
    rendered = str(value)
    if "\r" in rendered or "\n" in rendered:
        raise ValueError(f"{label} field {name!r} must not contain CR or LF")
    if rendered[:1] in {" ", "\t"} or rendered[-1:] in {" ", "\t"}:
        raise ValueError(
            f"{label} field {name!r} must not start or end with whitespace"
        )
    if any(ord(character) < 32 or ord(character) == 127 for character in rendered):
        raise ValueError(f"{label} field {name!r} must not contain control characters")
    try:
        rendered.encode("ascii")
    except UnicodeEncodeError as exc:
        raise ValueError(f"{label} field {name!r} must be ASCII encodable") from exc
    return rendered


def _serialized_parameter_values(
    adapter: TypeAdapter[Any],
    value: object,
    *,
    location: str,
    label: str,
) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise ConfigurationError(
            f"{label} serialization must produce an object with string field names"
        )
    mapping = cast(Mapping[object, object], value)
    if not all(isinstance(name, str) for name in mapping):
        raise ConfigurationError(
            f"{label} serialization must produce an object with string field names"
        )
    values = {cast(str, name): item for name, item in mapping.items()}
    schema = adapter.json_schema(mode="serialization", by_alias=True)
    declared = {
        name
        for name, _ in _request_parameter_fields(
            schema,
            location=location,
            label=label,
            check_required_nullable=False,
        )
    }
    if not set(values) <= declared:
        raise ConfigurationError(
            f"{label} serialization produced undeclared HTTP parameter fields"
        )
    return values


def _validate_parameter_round_trip(
    adapter: TypeAdapter[Any],
    validated: Any,
    wire_input: Mapping[str, object],
    *,
    label: str,
) -> None:
    try:
        round_tripped = adapter.validate_python(wire_input)
    except Exception as exc:
        raise ConfigurationError(
            f"{label} serialization does not validate after HTTP encoding; "
            "request parameter serializers must round-trip"
        ) from exc
    if not same_python_value(validated, round_tripped):
        raise ConfigurationError(
            f"{label} serialization changes the validated value after HTTP "
            "encoding; request parameter serializers must round-trip"
        )


def _parameter_serialization_copy(value: Any, *, label: str) -> Any:
    try:
        return deepcopy(value)
    except Exception as exc:
        raise ConfigurationError(
            f"{label} validated value cannot be isolated for safe serialization"
        ) from exc


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
) -> tuple[
    TypeAdapter[Any] | None,
    TypeAdapter[Any] | None,
    tuple[_ClientResponseDef, ...],
]:
    """Build every declared adapter before a request can leave the process."""
    response_adapter: TypeAdapter[Any] | None = None
    response_headers_adapter: TypeAdapter[Any] | None = None
    slots = ["params", "query", "headers", "request"]
    if not contract.responses:
        slots.extend(("response", "response_headers"))
    for slot in slots:
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
                serialization_schema = (
                    adapter.json_schema(mode="serialization", by_alias=True)
                    if slot in {"params", "query", "headers"}
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
            elif slot in {"params", "query", "headers"}:
                _request_parameter_fields(
                    schema,
                    location={
                        "params": "path",
                        "query": "query",
                        "headers": "header",
                    }[slot],
                    label=(f"{contract.name}: {slot} type {_type_name(annotation)}"),
                    serialization_schema=serialization_schema,
                )
                if slot == "headers" and contract.idempotency_key:
                    assert serialization_schema is not None
                    _validate_idempotency_key_header(
                        schema,
                        label=(
                            f"{contract.name}: headers type {_type_name(annotation)}"
                        ),
                        serialization_schema=serialization_schema,
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
    responses: list[_ClientResponseDef] = []
    for definition in contract.responses:
        case_response_adapter = (
            _adapter(definition.body, contract=contract, slot="response body")
            if definition.body is not None
            else None
        )
        case_headers_adapter = (
            _adapter(
                definition.headers,
                contract=contract,
                slot="response headers",
            )
            if definition.headers is not None
            else None
        )
        if case_headers_adapter is not None:
            _response_header_fields(
                case_headers_adapter.json_schema(mode="serialization", by_alias=True),
                label=f"{contract.name}: response status {definition.status} headers",
                validation_schema=case_headers_adapter.json_schema(
                    mode="validation", by_alias=True
                ),
            )
        responses.append(
            _ClientResponseDef(
                definition=definition,
                response_adapter=case_response_adapter,
                response_headers_adapter=case_headers_adapter,
            )
        )
    return response_adapter, response_headers_adapter, tuple(responses)


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
