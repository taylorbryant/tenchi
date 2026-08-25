from collections.abc import Mapping

from app.shared.users import User


class StaticTokenDirectory:
    def __init__(self, users: Mapping[str, User]) -> None:
        self._users = dict(users)

    async def lookup(self, token: str) -> User | None:
        return self._users.get(token)
