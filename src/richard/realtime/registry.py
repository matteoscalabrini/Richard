"""The open realtime sessions, so the rest of `richard serve` can reach a live
conversation. Perception offers context lines here; spec one adds server-initiated
turns (`prompt`, `say`) on the same registry."""
from __future__ import annotations

import threading


class SessionRegistry:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._sessions: list = []

    def add(self, session) -> None:
        with self._lock:
            if session not in self._sessions:
                self._sessions.append(session)

    def remove(self, session) -> None:
        with self._lock:
            if session in self._sessions:
                self._sessions.remove(session)

    def active(self) -> list:
        with self._lock:
            return list(self._sessions)

    def offer_context(self, line: str) -> bool:
        sessions = self.active()
        for session in sessions:
            session.add_context(line)
        return bool(sessions)
