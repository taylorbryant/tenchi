"""Signed inbound webhook bindings.

A webhook binding associates one HTTP contract with a verifier that sees the
exact, size-bounded request body before Tenchi parses or validates it. The
verifier authenticates the delivery (normally through a provider SDK or HMAC)
and may return an enriched application context carrying the verified service
identity. The ordinary contract remains the source of truth for the request
value passed to the use case.

Verification is part of the HTTP boundary. Verifiers may raise a declared
:class:`~tenchi.errors.AppError` for an expected rejection. Unexpected
failures remain framework-owned 500 responses, just like failures in ordinary
authentication hooks.
"""

from __future__ import annotations

import inspect
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any

from .contracts import Contract
from .errors import ConfigurationError

WebhookVerifier = Callable[["WebhookRequest", Any], Any]
"""A sync or async verifier for one exact inbound delivery.

Return ``None`` to keep the current application context or return a new context
that records the verified service identity. Raise a declared
:class:`~tenchi.errors.AppError` when a signature, timestamp, or provider
delivery check fails. Verifiers may be sync or async.
"""


class WebhookBindingError(ConfigurationError, TypeError):
    """A webhook declaration cannot safely verify its contract."""


@dataclass(frozen=True, slots=True)
class WebhookRequest:
    """The immutable request material available during verification.

    ``body`` is the exact byte sequence received from the client after
    Tenchi's request-size ceiling has been enforced. ``headers`` is read-only
    and keyed by lowercased HTTP names.
    """

    body: bytes
    headers: Mapping[str, str]
    header_values: Mapping[str, tuple[str, ...]]
    """All wire values for each lowercased header name, in arrival order."""
    contract: Contract[Any, Any]
    request_id: str


@dataclass(frozen=True, slots=True)
class Webhook:
    """One contract bound to a signed-delivery verifier."""

    contract: Contract[Any, Any]
    verifier: WebhookVerifier


def webhook(
    contract: Contract[Any, Any],
    verifier: WebhookVerifier,
) -> Webhook:
    """Bind a request-body contract to a sync or async verifier.

    Supply the returned binding to ``create_app(webhooks=[...])``. The
    verifier runs after ordinary authentication hooks and request-size/media
    checks, but before Pydantic request validation or the use case.
    """
    if not isinstance(contract, Contract):  # pyright: ignore[reportUnnecessaryIsInstance]
        raise WebhookBindingError(
            f"webhook: contract must be a Contract, got {type(contract).__name__}"
        )
    if contract.request is None:
        raise WebhookBindingError(
            f"webhook({contract.name!r}): contract must declare a request body"
        )
    if not contract.webhook:
        raise WebhookBindingError(
            f"webhook({contract.name!r}): contract must declare webhook=True"
        )
    _validate_verifier(contract.name, verifier)
    return Webhook(contract=contract, verifier=verifier)


def _validate_verifier(name: str, verifier: object) -> None:
    if not callable(verifier):
        raise WebhookBindingError(
            f"webhook({name!r}): verifier must be callable, got "
            f"{type(verifier).__name__}"
        )
    try:
        signature = inspect.signature(verifier)
    except (TypeError, ValueError) as exc:
        raise WebhookBindingError(
            f"webhook({name!r}): verifier has no inspectable signature: {exc}"
        ) from exc
    try:
        signature.bind(object(), object())
    except TypeError as exc:
        raise WebhookBindingError(
            f"webhook({name!r}): verifier must accept two positional arguments "
            f"(webhook_request, context): {exc}"
        ) from exc


__all__ = [
    "Webhook",
    "WebhookBindingError",
    "WebhookRequest",
    "WebhookVerifier",
    "webhook",
]
