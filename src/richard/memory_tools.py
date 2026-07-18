from __future__ import annotations

from richard.memory import MemoryStore

REMEMBER_SCHEMA = {
    "type": "function",
    "function": {
        "name": "remember",
        "description": "Save a durable fact about the user to long-term memory.",
        "parameters": {
            "type": "object",
            "properties": {"text": {"type": "string", "description": "The fact to remember."}},
            "required": ["text"],
        },
    },
}

FORGET_SCHEMA = {
    "type": "function",
    "function": {
        "name": "forget",
        "description": "Delete a remembered fact by its id.",
        "parameters": {
            "type": "object",
            "properties": {"id": {"type": "integer", "description": "The id of the memory to delete."}},
            "required": ["id"],
        },
    },
}


class MemoryTools:
    def __init__(self, store: MemoryStore) -> None:
        self._store = store

    def schemas(self) -> list[dict]:
        return [REMEMBER_SCHEMA, FORGET_SCHEMA]

    def execute(self, name: str, arguments: dict) -> str:
        if name == "remember":
            text = arguments.get("text")
            cleaned = str(text).strip() if text is not None else ""
            if not cleaned:
                return "Nothing to remember (no text given)."
            self._store.add(cleaned)
            return "Remembered."
        if name == "forget":
            raw_id = arguments.get("id")
            try:
                memory_id = int(raw_id)
            except (TypeError, ValueError):
                return f"Invalid memory id: {raw_id!r}."
            if self._store.remove(memory_id):
                return "Forgotten."
            return f"No memory with id {memory_id}."
        return f"Unknown tool: {name}."
