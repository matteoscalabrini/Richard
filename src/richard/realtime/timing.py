"""Per-turn latency marks for the realtime session.

One TurnTiming lives per turn. start() is called when the user stops speaking (or
when a typed turn is requested); marks record the first time each stage is reached.
The summary is logged once per turn so the pipeline can be measured from the log
instead of guessed. Marks set before start() or set twice are ignored on purpose:
the first token is the number that matters, not the last.
"""
from __future__ import annotations

import time
from collections.abc import Callable

ORDER = ("stt", "first_token", "first_audio")


class TurnTiming:
    def __init__(self, now: Callable[[], float] = time.monotonic) -> None:
        self._now = now
        self._t0: float | None = None
        self._marks: dict[str, float] = {}

    def start(self) -> None:
        self._t0 = self._now()
        self._marks = {}

    def mark(self, name: str) -> None:
        if self._t0 is None or name in self._marks:
            return
        self._marks[name] = self._now()

    def summary(self) -> dict[str, int]:
        if self._t0 is None:
            return {}
        ordered = [n for n in ORDER if n in self._marks] + [
            n for n in self._marks if n not in ORDER
        ]
        return {n: int(round((self._marks[n] - self._t0) * 1000)) for n in ordered}

    def line(self) -> str:
        summary = self.summary()
        if not summary:
            return ""
        return "turn timing: " + " ".join(f"{k}={v}ms" for k, v in summary.items())
