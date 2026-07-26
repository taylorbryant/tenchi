"""Signed inbound webhook verification at the HTTP composition boundary."""

import hashlib
import hmac
from dataclasses import replace

from app.features.projects.contracts import receive_member_added_webhook_contract
from app.features.projects.use_cases.receive_member_added_webhook import (
    MEMBER_DIRECTORY_SERVICE,
)
from app.server.context import AppContext
from app.shared.errors import invalid_webhook
from tenchi.errors import AppError, ConfigurationError
from tenchi.webhooks import Webhook, WebhookRequest, webhook

DEMO_WEBHOOK_SECRET = b"taskboard-development-secret"


def create_webhooks(secret: bytes) -> tuple[Webhook, ...]:
    """Bind the member-directory webhook to an exact-body HMAC verifier."""
    if not secret:
        raise ConfigurationError("member-directory webhook secret must not be empty")

    def verify(request: WebhookRequest, context: AppContext) -> AppContext:
        signatures = request.header_values.get("x-webhook-signature", ())
        if len(signatures) != 1:
            raise AppError(invalid_webhook)
        presented = signatures[0]
        expected = (
            "sha256="
            + hmac.new(
                secret,
                request.body,
                hashlib.sha256,
            ).hexdigest()
        )
        try:
            valid = hmac.compare_digest(
                presented.encode("ascii"),
                expected.encode("ascii"),
            )
        except UnicodeEncodeError:
            valid = False
        if not valid:
            raise AppError(invalid_webhook)
        return replace(context, service=MEMBER_DIRECTORY_SERVICE)

    return (webhook(receive_member_added_webhook_contract, verify),)
