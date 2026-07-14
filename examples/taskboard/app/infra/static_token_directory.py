from collections.abc import Mapping

from app.shared.users import User


class StaticTokenDirectory:
    """A fixed token table implementing the ``TokenDirectory`` port."""

    def __init__(self, tokens: Mapping[str, User]) -> None:
        self._tokens = dict(tokens)

    async def lookup(self, token: str) -> User | None:
        return self._tokens.get(token)
