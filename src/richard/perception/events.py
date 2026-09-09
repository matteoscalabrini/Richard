"""What the sensor says, in words the rest of Richard can use.

PresenceState turns per-frame detections into transitions with debounce: someone
must persist to have entered, be gone for a while to have left, and be recognised
twice to be named. The log keeps the episodes with timestamps; spec two turns them
into memories with provenance "observed".
"""
from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from richard.perception.detect import Detection

KINDS = frozenset({
    "person_entered", "person_left", "identified", "unknown_person",
    "motion_after_stillness", "scene_changed", "stillness",
})


@dataclass(frozen=True)
class PerceptionEvent:
    ts: float
    source_id: str
    kind: str
    subject: str = ""
    confidence: float = 1.0
    box: tuple[float, float, float, float] | None = None

    def to_dict(self) -> dict:
        return {"ts": self.ts, "source": self.source_id, "kind": self.kind, "subject": self.subject,
                "confidence": self.confidence, "box": list(self.box) if self.box else None}

    def line(self) -> str:
        who = "someone" if self.subject in ("", "unknown") else self.subject
        text = {
            "person_entered": f"{who} entered",
            "person_left": f"{who} left",
            "identified": f"{self.subject} recognised",
            "unknown_person": "an unknown person is here",
            "motion_after_stillness": "movement after a long stillness",
            "scene_changed": "the scene changed",
            "stillness": f"nothing has moved for {self.subject} minutes",
        }.get(self.kind, self.kind)
        return f"{text} ({self.source_id})"


@dataclass
class PresentPerson:
    subject: str
    since: float
    last_seen: float = 0.0
    votes: dict = field(default_factory=dict)
    unknown_reported: bool = False


class PresenceState:
    """One source's people, as a debounced state machine."""

    def __init__(self, source_id: str, *, enter_debounce_s: float = 2.0, leave_debounce_s: float = 10.0,
                 unknown_after_s: float = 20.0, report_unknown: bool = True) -> None:
        self.source_id = source_id
        self._enter = enter_debounce_s
        self._leave = leave_debounce_s
        self._unknown_after = unknown_after_s
        # "An unknown person" only means something when recognition is on; without a
        # gallery everyone is unknown and the event would be noise.
        self._report_unknown = report_unknown
        self._candidate_since: float | None = None
        self._person: PresentPerson | None = None  # one presence slot: "someone is here", named or not

    def present(self) -> list[PresentPerson]:
        return [self._person] if self._person is not None else []

    def observe(self, ts: float, persons: list[Detection], names: list[str | None]) -> list[PerceptionEvent]:
        events: list[PerceptionEvent] = []
        seen = bool(persons)
        known = [n for n in names if n]
        if self._person is None:
            if not seen:
                self._candidate_since = None
                return events
            if self._candidate_since is None:
                self._candidate_since = ts
            if ts - self._candidate_since < self._enter:
                return events
            self._person = PresentPerson(subject="unknown", since=self._candidate_since, last_seen=ts)
            events.append(PerceptionEvent(ts, self.source_id, "person_entered", "unknown", persons[0].score, persons[0].box))
        person = self._person
        if seen:
            person.last_seen = ts
            for name in known:
                person.votes[name] = person.votes.get(name, 0) + 1
                if person.votes[name] >= 2 and person.subject != name:
                    person.subject = name
                    events.append(PerceptionEvent(ts, self.source_id, "identified", name, 0.9, persons[0].box))
            if (self._report_unknown and person.subject == "unknown" and not person.unknown_reported
                    and ts - person.since >= self._unknown_after):
                person.unknown_reported = True
                events.append(PerceptionEvent(ts, self.source_id, "unknown_person", "unknown", persons[0].score, persons[0].box))
            return events
        if ts - person.last_seen >= self._leave:
            events.append(PerceptionEvent(ts, self.source_id, "person_left", person.subject))
            self._person = None
            self._candidate_since = None
        return events


class PresenceLog:
    def __init__(self, path: Path | str) -> None:
        self._path = str(path)
        if self._path != ":memory:":
            Path(self._path).parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(self._path, check_same_thread=False)
        self._conn.execute(
            "CREATE TABLE IF NOT EXISTS perception_log (id INTEGER PRIMARY KEY AUTOINCREMENT, at TEXT NOT NULL, "
            "ts REAL NOT NULL, source TEXT NOT NULL, kind TEXT NOT NULL, subject TEXT NOT NULL, "
            "confidence REAL NOT NULL, thumbnail BLOB)")
        self._conn.commit()

    def append(self, event: PerceptionEvent, thumbnail: bytes | None = None) -> int:
        cur = self._conn.execute(
            "INSERT INTO perception_log (at, ts, source, kind, subject, confidence, thumbnail) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (datetime.now(timezone.utc).isoformat(timespec="seconds"), event.ts, event.source_id, event.kind,
             event.subject, event.confidence, thumbnail))
        self._conn.commit()
        return int(cur.lastrowid)

    @staticmethod
    def _row(r) -> dict:
        return {"id": r[0], "at": r[1], "ts": r[2], "source": r[3], "kind": r[4], "subject": r[5],
                "confidence": r[6], "has_thumbnail": r[7] is not None}

    def recent(self, since_id: int = 0, limit: int = 100) -> list[dict]:
        rows = self._conn.execute(
            "SELECT id, at, ts, source, kind, subject, confidence, thumbnail FROM perception_log "
            "WHERE id > ? ORDER BY id DESC LIMIT ?", (since_id, limit)).fetchall()
        return [self._row(r) for r in reversed(rows)]

    def last_seen(self, subject: str) -> dict | None:
        r = self._conn.execute(
            "SELECT id, at, ts, source, kind, subject, confidence, thumbnail FROM perception_log "
            "WHERE subject = ? AND kind IN ('identified', 'person_entered', 'person_left') ORDER BY id DESC LIMIT 1",
            (subject,)).fetchone()
        return self._row(r) if r else None

    def thumbnail(self, event_id: int) -> bytes | None:
        r = self._conn.execute("SELECT thumbnail FROM perception_log WHERE id = ?", (event_id,)).fetchone()
        return r[0] if r else None

    def close(self) -> None:
        self._conn.close()
