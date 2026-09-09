"""The open realtime sessions, so the rest of `richard serve` can reach a live
conversation. Perception offers context lines here; spec one adds server-initiated
turns (`prompt`, `say`) on the same registry."""
from __future__ import annotations

import logging
import threading

log = logging.getLogger("richard.realtime")


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

    def offer_context(self, line: str, wake: bool = False) -> bool:
        """Queue a context line on every open session; with `wake`, ask each idle session
        to run an unsolicited turn on it now (busy sessions keep it for their next turn)."""
        sessions = self.active()
        log.info("perception context offered to %d session(s) (wake=%s): %s", len(sessions), wake, line)
        for session in sessions:
            session.add_context(line)
            if wake:
                session.wake()
        return bool(sessions)
