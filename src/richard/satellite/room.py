from __future__ import annotations


class RoomContextProvider:
    """Injects the speaking hub's room as the 'here' context for one session. No tools."""

    def __init__(self, room_name: str | None) -> None:
        self._room = room_name

    def schemas(self) -> list[dict]:
        return []

    def execute(self, name: str, arguments: dict) -> str:
        return f"Unknown tool: {name}."

    def context(self) -> str | None:
        if not self._room:
            return None
        return (
            f"You are speaking to the user through the {self._room} hub. "
            f'"Here" means the {self._room}; an unqualified device such as "the light" '
            f"refers to the one in the {self._room}."
        )
