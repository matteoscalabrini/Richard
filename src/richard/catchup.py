"""Catch-up digest: what happened while nobody was talking to Richard.

A pure builder (`build_digest`) turns perception events, control-loop notifications
and a live Home Assistant snapshot into at most ten short bullet lines, so a fresh
realtime session can be handed one background message instead of cold-starting.
`SessionState` remembers, across processes, when the previous session ended and the
last perception/notification ids seen, so the next build only looks at what's new.
"""
from __future__ import annotations

import json
import os
import tempfile
from datetime import datetime
from pathlib import Path

from richard.clock import stamp
from richard.perception.events import PerceptionEvent

_MAX_BULLETS = 10
_MAX_CHARS = 600
_PERSON_KINDS = frozenset({"person_entered", "person_left", "identified", "unknown_person"})
_NOISY_KINDS = ("scene_changed", "stillness", "motion_after_stillness")
_NOISY_LABELS = {
    "scene_changed": "camera view changed {n} times",
    "stillness": "{n} still periods",
    "motion_after_stillness": "movement after stillness {n} times",
}


def default_session_state_path() -> Path:
    return Path.home() / ".richard" / "session_state.json"


class SessionState:
    """Small JSON state file under `~/.richard/`, holding when the last session ended
    and the last perception/notification ids the digest builder has already used."""

    _DEFAULTS = {"last_ended_at": None, "last_perception_id": 0, "last_notification_id": 0}

    def __init__(self, path: Path | str) -> None:
        self._path = Path(path)

    def read(self) -> dict:
        try:
            data = json.loads(self._path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return dict(self._DEFAULTS)
        if not isinstance(data, dict):
            return dict(self._DEFAULTS)
        result = dict(self._DEFAULTS)
        for key in result:
            if key in data:
                result[key] = data[key]
        return result

    def write(self, *, last_ended_at: str | None, last_perception_id: int, last_notification_id: int) -> None:
        payload = {
            "last_ended_at": last_ended_at,
            "last_perception_id": last_perception_id,
            "last_notification_id": last_notification_id,
        }
        self._path.parent.mkdir(parents=True, exist_ok=True)
        descriptor, temp_name = tempfile.mkstemp(prefix=f".{self._path.stem}.", suffix=".tmp",
                                                   dir=self._path.parent)
        temp_path = Path(temp_name)
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
                json.dump(payload, stream)
                stream.flush()
                os.fsync(stream.fileno())
            os.chmod(temp_path, 0o600)
            os.replace(temp_path, self._path)
        finally:
            temp_path.unlink(missing_ok=True)


def _ago(now: datetime, last_ended_at: str) -> str:
    then = datetime.fromisoformat(last_ended_at)
    delta_seconds = (now - then).total_seconds()
    minutes = round(delta_seconds / 60)
    if minutes < 60:
        return f"{minutes} min ago"
    hours = round(delta_seconds / 3600)
    if hours < 48:
        return f"{hours} h ago"
    days = round(delta_seconds / 86400)
    return f"{days} days ago"


def _header(now: datetime, tz_name: str | None, last_ended_at: str) -> str:
    local = stamp(last_ended_at, tz_name)
    ago = _ago(now, last_ended_at)
    return (f"[catch-up] Background since the last conversation ended {local} ({ago}). "
            "Use it only to answer questions or ground what you say; do not report it unless asked.")


def _person_bullets(perception_events: list[dict], tz_name: str | None) -> list[str]:
    person_events = [e for e in perception_events if e.get("kind") in _PERSON_KINDS]
    newest_six = person_events[-6:]
    lines = []
    for event in newest_six:
        hhmm = stamp(event["at"], tz_name)[-5:]
        text = PerceptionEvent(0.0, event.get("source", ""), event["kind"], event.get("subject", "")).line()
        lines.append(f"- {hhmm} {text}")
    return lines


def _noisy_bullet(perception_events: list[dict]) -> str | None:
    counts = {kind: 0 for kind in _NOISY_KINDS}
    for event in perception_events:
        kind = event.get("kind")
        if kind in counts:
            counts[kind] += 1
    parts = [_NOISY_LABELS[kind].format(n=counts[kind]) for kind in _NOISY_KINDS if counts[kind] > 0]
    if not parts:
        return None
    return "- " + "; ".join(parts)


def _notification_bullets(notifications: list[dict], tz_name: str | None) -> list[str]:
    newest_three = notifications[-3:]
    lines = []
    for note in newest_three:
        hhmm = stamp(note["created_at"], tz_name)[-5:]
        summary = note.get("summary", "")
        if len(summary) > 80:
            summary = summary[:80]
        lines.append(f'- loop "{note.get("loop_name", "")}" {hhmm}: {summary}')
    return lines


def build_digest(*, now: datetime, tz_name: str | None, last_ended_at: str | None,
                  perception_events: list[dict], notifications: list[dict],
                  lights_on: list[str] | None, present: list[dict]) -> str | None:
    if last_ended_at is None:
        return None
    if not perception_events and not notifications and not present and lights_on is None:
        return None

    bullets = _person_bullets(perception_events, tz_name)
    noisy = _noisy_bullet(perception_events)
    if noisy is not None:
        bullets.append(noisy)
    bullets.extend(_notification_bullets(notifications, tz_name))
    if lights_on is not None:
        if lights_on:
            names = ", ".join(lights_on[:5])
            bullets.append(f"- lights on now: {names}")
        else:
            bullets.append("- no lights on now")
    if present:
        names = ", ".join(f"{p.get('subject', '')} ({p.get('source', '')})" for p in present)
        bullets.append(f"- in view now: {names}")

    header = _header(now, tz_name, last_ended_at)

    while True:
        text = "\n".join([header, *bullets[:_MAX_BULLETS]])
        if len(bullets) <= _MAX_BULLETS and len(text) <= _MAX_CHARS:
            return text
        if not bullets:
            return text
        bullets = bullets[:-1]
