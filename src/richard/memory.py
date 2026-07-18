from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path


@dataclass(frozen=True)
class Memory:
    id: int
    text: str
    created_at: str
    person: str


def default_memory_path() -> Path:
    return Path.home() / ".richard" / "memory.db"


class MemoryStore:
    def __init__(self, path: Path | str) -> None:
        self._path = str(path)
        if self._path != ":memory:":
            Path(self._path).parent.mkdir(parents=True, exist_ok=True)
        # check_same_thread=False: in the satellite path the store is built on the event-loop
        # thread but read during a turn on a run_in_executor worker thread (matches DeviceRegistry).
        self._conn = sqlite3.connect(self._path, check_same_thread=False)
        self._conn.execute(
            "CREATE TABLE IF NOT EXISTS memories ("
            "id INTEGER PRIMARY KEY AUTOINCREMENT, "
            "text TEXT NOT NULL, "
            "created_at TEXT NOT NULL, "
            "person TEXT NOT NULL DEFAULT 'you')"
        )
        self._conn.commit()

    def add(self, text: str, person: str = "you") -> Memory:
        created_at = datetime.now(timezone.utc).isoformat()
        cur = self._conn.execute(
            "INSERT INTO memories (text, created_at, person) VALUES (?, ?, ?)",
            (text, created_at, person),
        )
        self._conn.commit()
        return Memory(id=int(cur.lastrowid), text=text, created_at=created_at, person=person)

    def remove(self, memory_id: int) -> bool:
        cur = self._conn.execute("DELETE FROM memories WHERE id = ?", (memory_id,))
        self._conn.commit()
        return cur.rowcount > 0

    def all(self, person: str | None = None) -> list[Memory]:
        if person is None:
            rows = self._conn.execute(
                "SELECT id, text, created_at, person FROM memories ORDER BY id"
            ).fetchall()
        else:
            rows = self._conn.execute(
                "SELECT id, text, created_at, person FROM memories WHERE person = ? ORDER BY id",
                (person,),
            ).fetchall()
        return [Memory(id=r[0], text=r[1], created_at=r[2], person=r[3]) for r in rows]

    def close(self) -> None:
        self._conn.close()
