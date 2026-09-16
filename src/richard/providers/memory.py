from __future__ import annotations

from richard.clock import stamp
from richard.memory import MemoryStore
from richard.memory_tools import MemoryTools

MEMORY_INTRO = (
    "You remember things about the user across conversations. Use the remember tool to save "
    "anything worth keeping, and the forget tool (by id) to remove something. Each memory is "
    "prefixed with the date and time it was saved."
)


class MemoryProvider:
    def __init__(self, store: MemoryStore, *, tz_name: str | None = None) -> None:
        self._store = store
        self._tools = MemoryTools(store)
        self._tz_name = tz_name

    def schemas(self) -> list[dict]:
        return self._tools.schemas()

    def execute(self, name: str, arguments: dict) -> str:
        return self._tools.execute(name, arguments)

    def context_revision(self) -> int:
        """Bumped when a memory is revoked, so a pinned system head still carrying the
        deleted fact is rebuilt on its next turn. Adding a memory does not bump it."""
        return self._store.revision

    def context(self) -> str | None:
        memories = self._store.all()
        if memories:
            lines = "\n".join(self._line(m) for m in memories)
        else:
            lines = "Nothing yet."
        return f"{MEMORY_INTRO}\n\nWhat you remember about the user:\n{lines}"

    def _line(self, m) -> str:
        when = stamp(m.created_at, self._tz_name)
        if when:
            return f"- [{m.id}] {when} — {m.text}"
        return f"- [{m.id}] {m.text}"
