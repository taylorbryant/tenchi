"""Shared-kernel identity concepts used across features."""

from dataclasses import dataclass
from typing import Protocol

from pydantic import BaseModel

from tenchi.errors import AppError

from .errors import unauthorized


class User(BaseModel):
    id: str
    name: str


@dataclass(frozen=True, slots=True)
class OwnerScope:
    """Proof that an owner id came from an authenticated user.

    Owner-scoped repository methods accept this instead of a raw string,
    so an id lifted from request input cannot be passed by accident —
    derive it with :func:`require_owner_scope` (or ``owner_scope``).
    """

    owner_id: str


def owner_scope(user: User) -> OwnerScope:
    return OwnerScope(owner_id=user.id)


class TokenDirectory(Protocol):
    """Resolves a bearer token to a user, or ``None`` when unknown."""

    async def lookup(self, token: str) -> User | None: ...


def require_user(user: User | None) -> User:
    """Assert an authenticated user inside a use case.

    The HTTP hook authenticates; use cases still assert identity for rules
    that matter outside HTTP (direct calls, tests, future workers).
    """
    if user is None:
        raise AppError(unauthorized)
    return user


def require_owner_scope(user: User | None) -> OwnerScope:
    """Assert an authenticated user and return their owner scope."""
    return owner_scope(require_user(user))
