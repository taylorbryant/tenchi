"""Shared identity concepts for owner-scoped knowledge."""

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
    """Proof that an owner id came from authenticated identity."""

    owner_id: str


class TokenDirectory(Protocol):
    async def lookup(self, token: str) -> User | None: ...


def owner_scope(user: User) -> OwnerScope:
    return OwnerScope(owner_id=user.id)


def require_user(user: User | None) -> User:
    if user is None:
        raise AppError(unauthorized)
    return user


def require_owner_scope(user: User | None) -> OwnerScope:
    return owner_scope(require_user(user))
