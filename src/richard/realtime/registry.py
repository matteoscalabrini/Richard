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

    def offer_context(self, line: str, wake: bool = False, *,
                      source_id: str | None = None, kind: str | None = None) -> bool:
        """Route perception context to its source-bound session.

        Unbound clients retain the legacy all-source context stream. Scene changes
        become wake opportunities only for source-bound visual clients.
        """
        sessions = self.active()
        log.info("perception context offered to %d session(s) (wake=%s): %s", len(sessions), wake, line)
        taken = False
        for session in sessions:
            bound = getattr(session, "source_id", None)
            visual = bool(getattr(session, "visual_context", False))
            if bound is not None and (not visual or source_id != bound):
                continue
            session.add_context(line)
            taken = True
            if wake or (kind == "scene_changed" and bound is not None and visual):
                session.wake()
        return taken
