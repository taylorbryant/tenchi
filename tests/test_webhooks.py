"""Exact-body verification for signed inbound HTTP deliveries."""

import asyncio
import hashlib
import hmac
from collections.abc import AsyncGenerator, Sequence
from contextlib import asynccontextmanager
from dataclasses import dataclass

import httpx
import pytest
from pydantic import BaseModel
from starlette.applications import Starlette

from tenchi.contracts import contract
from tenchi.errors import ERROR_SOURCE_HEADER, AppError, ConfigurationError, ErrorDef
from tenchi.routes import RouteGroup, route, route_group
from tenchi.server import Hook, RequestInfo, create_app
from tenchi.webhooks import (
    Webhook,
    WebhookBindingError,
    WebhookRequest,
    WebhookVerifier,
    webhook,
)


class Delivery(BaseModel):
    event_id: str
    action: str


@dataclass(frozen=True, slots=True)
class Context:
    marker: str = "request"


invalid_webhook = ErrorDef(
    code="INVALID_WEBHOOK",
    status=401,
    message="Webhook verification failed",
)
unauthorized = ErrorDef(
    code="UNAUTHORIZED",
    status=401,
    message="Unauthorized",
)

delivery_contract = contract(
    method="POST",
    path="/deliveries",
    request=Delivery,
    response=Delivery,
    errors=(invalid_webhook,),
    name="deliveries.receive",
    public=True,
    webhook=True,
)


async def receive_delivery(request: Delivery, context: Context) -> Delivery:
    return request


def signed_verifier(
    secret: bytes,
    seen: list[WebhookRequest],
) -> WebhookVerifier:
    def verify(request: WebhookRequest, context: Context) -> None:
        seen.append(request)
        signature = request.headers.get("x-webhook-signature", "")
        expected = hmac.new(secret, request.body, hashlib.sha256).hexdigest()
        if not hmac.compare_digest(signature, expected):
            raise AppError(invalid_webhook)

    return verify


def make_app(
    verifier: WebhookVerifier,
    *,
    routes: RouteGroup | None = None,
    hooks: Sequence[Hook] = (),
    max_request_bytes: int | None = 1_048_576,
) -> Starlette:
    selected_routes = routes or route_group(route(delivery_contract, receive_delivery))
    binding = webhook(delivery_contract, verifier)
    return create_app(
        routes=selected_routes,
        context_factory=Context,
        hooks=hooks,
        webhooks=[binding],
        max_request_bytes=max_request_bytes,
    )


async def test_webhook_verifies_exact_bounded_bytes_before_validation() -> None:
    secret = b"development-secret"
    seen: list[WebhookRequest] = []
    app = make_app(signed_verifier(secret, seen))
    body = b'{ "event_id": "evt_1", "action": "opened" }\n'
    signature = hmac.new(secret, body, hashlib.sha256).hexdigest()

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://testserver",
    ) as client:
        response = await client.post(
            "/deliveries",
            content=body,
            headers={
                "content-type": "application/json",
                "x-webhook-signature": signature,
            },
        )

    assert response.status_code == 200
    assert response.json() == {"event_id": "evt_1", "action": "opened"}
    assert seen[0].body == body
    assert seen[0].headers["x-webhook-signature"] == signature
    assert seen[0].header_values["x-webhook-signature"] == (signature,)
    assert seen[0].contract.name == "deliveries.receive"
    assert seen[0].request_id == response.headers["x-request-id"]
    with pytest.raises(TypeError):
        seen[0].headers["x-added"] = "unsafe"  # type: ignore[index]
    with pytest.raises(TypeError):
        seen[0].header_values["x-added"] = ("unsafe",)  # type: ignore[index]


async def test_webhook_preserves_repeated_header_values() -> None:
    seen: list[tuple[str, ...]] = []

    def verify(request: WebhookRequest, context: Context) -> None:
        seen.append(request.header_values["x-webhook-signature"])

    app = make_app(verify)

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://testserver",
    ) as client:
        response = await client.post(
            "/deliveries",
            json={"event_id": "evt_headers", "action": "opened"},
            headers=[
                ("x-webhook-signature", "first"),
                ("x-webhook-signature", "second"),
            ],
        )

    assert response.status_code == 200
    assert seen == [("first", "second")]


async def test_invalid_signature_wins_over_all_contract_input_validation() -> None:
    invoked = False

    class DeliveryHeaders(BaseModel):
        delivery_kind: str

    guarded_contract = contract(
        method="POST",
        path="/guarded-deliveries",
        headers=DeliveryHeaders,
        request=Delivery,
        response=Delivery,
        errors=(invalid_webhook,),
        name="deliveries.guarded",
        public=True,
        webhook=True,
    )

    async def use_case(
        headers: DeliveryHeaders,
        request: Delivery,
        context: Context,
    ) -> Delivery:
        nonlocal invoked
        invoked = True
        return request

    app = create_app(
        routes=route_group(route(guarded_contract, use_case)),
        context_factory=Context,
        webhooks=[webhook(guarded_contract, signed_verifier(b"secret", []))],
    )

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://testserver",
    ) as client:
        response = await client.post(
            "/guarded-deliveries",
            content=b"not json",
            headers={
                "content-type": "application/json",
                "x-webhook-signature": "invalid",
            },
        )

    assert response.status_code == 401
    assert response.headers[ERROR_SOURCE_HEADER] == "app"
    assert response.json()["code"] == "INVALID_WEBHOOK"
    assert not invoked


async def test_async_verifier_runs_before_the_use_case() -> None:
    order: list[str] = []

    async def verify(request: WebhookRequest, context: Context) -> None:
        await asyncio.sleep(0)
        order.append("verify")

    async def use_case(request: Delivery, context: Context) -> Delivery:
        order.append(f"use-case:{request.event_id}")
        return request

    app = create_app(
        routes=route_group(route(delivery_contract, use_case)),
        context_factory=Context,
        webhooks=[webhook(delivery_contract, verify)],
    )

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://testserver",
    ) as client:
        response = await client.post(
            "/deliveries",
            json={"event_id": "evt_2", "action": "closed"},
        )

    assert response.status_code == 200
    assert order == ["verify", "use-case:evt_2"]


async def test_header_hook_rejection_happens_before_body_verification() -> None:
    verified = False

    def authenticate(info: RequestInfo, context: Context) -> None:
        raise AppError(unauthorized)

    def verify(request: WebhookRequest, context: Context) -> None:
        nonlocal verified
        verified = True

    routes = route_group(
        route(delivery_contract, receive_delivery),
        errors=(unauthorized,),
    )
    app = make_app(verify, routes=routes, hooks=[authenticate])

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://testserver",
    ) as client:
        response = await client.post(
            "/deliveries",
            json={"event_id": "evt_3", "action": "opened"},
        )

    assert response.status_code == 401
    assert response.json()["code"] == "UNAUTHORIZED"
    assert not verified


async def test_size_and_media_checks_happen_before_verification() -> None:
    verified: list[bytes] = []

    def verify(request: WebhookRequest, context: Context) -> None:
        verified.append(request.body)

    app = make_app(verify, max_request_bytes=8)

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://testserver",
    ) as client:
        too_large = await client.post(
            "/deliveries",
            content=b'{"event_id":"too-large"}',
            headers={"content-type": "application/json"},
        )
        wrong_media = await client.post(
            "/deliveries",
            content=b"small",
            headers={"content-type": "text/plain"},
        )

    assert too_large.status_code == 413
    assert wrong_media.status_code == 415
    assert verified == []


async def test_verifier_failure_exits_request_scope_before_error_mapping() -> None:
    exits: list[type[BaseException] | None] = []

    @asynccontextmanager
    async def context_factory() -> AsyncGenerator[Context]:
        try:
            yield Context()
        except BaseException as exc:
            exits.append(type(exc))
            raise
        else:
            exits.append(None)

    def reject(request: WebhookRequest, context: Context) -> None:
        raise AppError(invalid_webhook)

    app = create_app(
        routes=route_group(route(delivery_contract, receive_delivery)),
        context_factory=context_factory,
        webhooks=[webhook(delivery_contract, reject)],
    )

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://testserver",
    ) as client:
        response = await client.post(
            "/deliveries",
            json={"event_id": "evt_4", "action": "opened"},
        )

    assert response.status_code == 401
    assert exits == [AppError]


async def test_undeclared_verifier_error_remains_a_framework_failure() -> None:
    def reject(request: WebhookRequest, context: Context) -> None:
        raise AppError(unauthorized)

    app = make_app(reject)

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://testserver",
    ) as client:
        response = await client.post(
            "/deliveries",
            json={"event_id": "evt_undeclared", "action": "opened"},
        )

    assert response.status_code == 500
    assert response.headers[ERROR_SOURCE_HEADER] == "framework"
    assert response.json()["code"] == "INTERNAL_SERVER_ERROR"


async def test_verifier_is_covered_by_the_contract_timeout() -> None:
    cleaned_up = asyncio.Event()
    timed_contract = contract(
        method="POST",
        path="/timed",
        request=Delivery,
        response=Delivery,
        timeout=0.01,
        name="deliveries.timed",
        webhook=True,
    )

    async def verify(request: WebhookRequest, context: Context) -> None:
        try:
            await asyncio.Event().wait()
        finally:
            cleaned_up.set()

    app = create_app(
        routes=route_group(route(timed_contract, receive_delivery)),
        context_factory=Context,
        webhooks=[webhook(timed_contract, verify)],
    )

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://testserver",
    ) as client:
        response = await client.post(
            "/timed",
            json={"event_id": "evt_5", "action": "opened"},
        )

    assert response.status_code == 504
    assert cleaned_up.is_set()


async def test_verifier_can_attach_service_identity_to_context() -> None:
    verified_context = Context(marker="verified-provider")

    def verify(request: WebhookRequest, context: Context) -> Context:
        return verified_context

    async def use_case(request: Delivery, context: Context) -> Delivery:
        assert context is verified_context
        return request

    app = create_app(
        routes=route_group(route(delivery_contract, use_case)),
        context_factory=Context,
        webhooks=[webhook(delivery_contract, verify)],
    )

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://testserver",
    ) as client:
        response = await client.post(
            "/deliveries",
            json={"event_id": "evt_6", "action": "opened"},
        )

    assert response.status_code == 200


def test_webhook_binding_validates_contract_and_verifier_shape() -> None:
    unsigned = contract(method="POST", path="/unsigned", request=Delivery)

    with pytest.raises(WebhookBindingError, match="must declare webhook=True"):
        webhook(unsigned, lambda request, context: None)
    with pytest.raises(WebhookBindingError, match="must accept two positional"):
        webhook(delivery_contract, lambda: None)  # type: ignore[arg-type]
    with pytest.raises(WebhookBindingError, match="must be callable"):
        webhook(delivery_contract, object())  # type: ignore[arg-type]


def test_create_app_rejects_unknown_duplicate_and_forged_bindings() -> None:
    other_contract = contract(
        method="POST",
        path="/other",
        request=Delivery,
        response=Delivery,
        name="deliveries.other",
        webhook=True,
    )
    binding = webhook(delivery_contract, lambda request, context: None)

    with pytest.raises(ConfigurationError, match="is not present in routes"):
        create_app(
            routes=route_group(route(other_contract, receive_delivery)),
            context_factory=Context,
            webhooks=[binding],
        )
    with pytest.raises(ConfigurationError, match="duplicate webhook binding"):
        create_app(
            routes=route_group(route(delivery_contract, receive_delivery)),
            context_factory=Context,
            webhooks=[binding, binding],
        )

    requestless = contract(method="POST", path="/requestless")

    async def no_request(context: Context) -> None:
        return None

    forged = Webhook(
        contract=requestless,
        verifier=lambda request, context: None,
    )
    with pytest.raises(ConfigurationError, match="must declare a request body"):
        create_app(
            routes=route_group(route(requestless, no_request)),
            context_factory=Context,
            webhooks=[forged],
        )


def test_create_app_requires_every_webhook_contract_to_be_bound() -> None:
    with pytest.raises(ConfigurationError, match="require verifier bindings"):
        create_app(
            routes=route_group(route(delivery_contract, receive_delivery)),
            context_factory=Context,
        )


async def test_binding_survives_route_group_prefix_and_error_amendments() -> None:
    seen: list[WebhookRequest] = []
    feature_routes = route_group(route(delivery_contract, receive_delivery))
    mounted = route_group(
        route_group(feature_routes, prefix="/v1"),
        route_group(feature_routes, prefix="/v2"),
        errors=(unauthorized,),
    )
    app = create_app(
        routes=mounted,
        context_factory=Context,
        webhooks=[
            webhook(
                delivery_contract,
                lambda request, context: seen.append(request),
            )
        ],
    )

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://testserver",
    ) as client:
        first = await client.post(
            "/v1/deliveries",
            json={"event_id": "evt_7", "action": "opened"},
        )
        second = await client.post(
            "/v2/deliveries",
            json={"event_id": "evt_8", "action": "opened"},
        )

    assert first.status_code == 200
    assert second.status_code == 200
    assert [request.contract.path for request in seen] == [
        "/v1/deliveries",
        "/v2/deliveries",
    ]
