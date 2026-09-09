"""The gate filters repetition, not relevance.

Debounce and "transitions only" already happened in PresenceState and the motion
stage; here: a master switch, quiet hours, and a cooldown per (kind, subject) so a
person pacing in and out of frame does not wake the brain every time. Relevance
(is this worth a word?) belongs to the brain and, later, spec four's policy.
"""
from __future__ import annotations

import time
from dataclasses import dataclass
from datetime import datetime

from richard.perception.events import PerceptionEvent


@dataclass
class GatePolicy:
    enabled: bool = True
    quiet_hours: str = ""
    cooldown_s: float = 120.0


def parse_quiet_hours(text: str):
    text = (text or "").strip()
    if not text:
        return None
    try:
        start, end = text.split("-")
        sh, sm = (int(v) for v in start.split(":"))
        eh, em = (int(v) for v in end.split(":"))
    except ValueError as exc:
        raise ValueError("quiet_hours must look like 23:00-07:30") from exc
    for h, m in ((sh, sm), (eh, em)):
        if not (0 <= h < 24 and 0 <= m < 60):
            raise ValueError("quiet_hours must look like 23:00-07:30")
    return ((sh, sm), (eh, em))


def in_quiet_hours(policy: GatePolicy, now: datetime) -> bool:
    window = parse_quiet_hours(policy.quiet_hours)
    if window is None:
        return False
    (sh, sm), (eh, em) = window
    minute = now.hour * 60 + now.minute
    start, end = sh * 60 + sm, eh * 60 + em
    if start <= end:
        return start <= minute < end
    return minute >= start or minute < end  # wraps midnight


class Gate:
    def __init__(self, policy: GatePolicy, *, clock=time.monotonic, wall=datetime.now) -> None:
        self.policy = policy
        self._clock = clock
        self._wall = wall
        self._last: dict[tuple[str, str], float] = {}
        self.reasons: list[tuple[str, str]] = []

    def admit(self, events: list[PerceptionEvent]) -> list[PerceptionEvent]:
        self.reasons = []
        admitted: list[PerceptionEvent] = []
        now = self._clock()
        quiet = in_quiet_hours(self.policy, self._wall())
        for event in events:
            if not self.policy.enabled:
                self.reasons.append((event.kind, "disabled"))
                continue
            if quiet:
                self.reasons.append((event.kind, "quiet_hours"))
                continue
            key = (event.kind, event.subject)
            last = self._last.get(key)
            if last is not None and now - last < self.policy.cooldown_s:
                self.reasons.append((event.kind, "cooldown"))
                continue
            self._last[key] = now
            admitted.append(event)
        return admitted
