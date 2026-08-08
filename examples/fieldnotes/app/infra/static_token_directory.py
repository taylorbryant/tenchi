from collections.abc import Mapping

from app.shared.users import User

DEMO_TOKENS: Mapping[str, User] = {
    "alice-token": User(id="alice", name="Alice"),
    "bob-token": User(id="bob", name="Bob"),
}


class StaticTokenDirectory:
    def __init__(self, tokens: Mapping[str, User]) -> None:
        self._tokens = dict(tokens)

    async def lookup(self, token: str) -> User | None:
        return self._tokens.get(token)
