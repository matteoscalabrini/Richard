from __future__ import annotations

import threading

from richard.satellite.connection import RelayConnection


class RelayRegistry:
    """Live map of relay_id → connection. Thread-safe (the transport runs connections
    on their own tasks/threads)."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._conns: dict[str, RelayConnection] = {}

    def register(self, relay_id: str, conn: RelayConnection) -> None:
        with self._lock:
            self._conns[relay_id] = conn

    def unregister(self, relay_id: str) -> None:
        with self._lock:
            self._conns.pop(relay_id, None)

    def get(self, relay_id: str | None) -> RelayConnection | None:
        if relay_id is None:
            return None
        with self._lock:
            return self._conns.get(relay_id)

    def ids(self) -> list[str]:
        with self._lock:
            return list(self._conns.keys())
