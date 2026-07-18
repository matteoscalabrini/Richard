from __future__ import annotations

from richard.memory import MemoryStore
from richard.memory_tools import MemoryTools

MEMORY_INTRO = (
    "You remember things about the user across conversations. Use the remember tool to save "
    "anything worth keeping, and the forget tool (by id) to remove something."
)


class MemoryProvider:
    def __init__(self, store: MemoryStore) -> None:
        self._store = store
        self._tools = MemoryTools(store)

    def schemas(self) -> list[dict]:
        return self._tools.schemas()

    def execute(self, name: str, arguments: dict) -> str:
        return self._tools.execute(name, arguments)

    def context(self) -> str | None:
        memories = self._store.all()
        if memories:
            lines = "\n".join(f"- [{m.id}] {m.text}" for m in memories)
        else:
            lines = "Nothing yet."
        return f"{MEMORY_INTRO}\n\nWhat you remember about the user:\n{lines}"
