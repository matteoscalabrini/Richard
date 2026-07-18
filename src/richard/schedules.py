from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta

_DAY_NAMES = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")
_MIN_EVERY_SECONDS = 60.0
_MAX_EVERY_SECONDS = 604800.0


@dataclass(frozen=True)
class Schedule:
    """One of three shapes: daily/weekly 'at', recurring 'every', one-shot 'once'."""

    at: str | None = None
    days: tuple[str, ...] = ()
    every_seconds: float | None = None
    once_at: str | None = None

    @property
    def kind(self) -> str:
        if self.at is not None:
            return "at"
        if self.every_seconds is not None:
            return "every"
        return "once"

    def to_payload(self) -> dict:
        if self.kind == "at":
            payload: dict = {"at": self.at}
            if self.days:
                payload["days"] = list(self.days)
            return payload
        if self.kind == "every":
            return {"every_seconds": self.every_seconds}
        return {"once_at": self.once_at}


def parse_schedule(payload: object) -> Schedule:
    if not isinstance(payload, dict) or not payload:
        raise ValueError(
            "schedule must be an object with exactly one of: at, every_seconds, once_at"
        )
    shapes = {"at", "every_seconds", "once_at"} & set(payload)
    if len(shapes) != 1:
        raise ValueError("schedule must contain exactly one of: at, every_seconds, once_at")
    extra = set(payload) - {"at", "days", "every_seconds", "once_at"}
    if extra:
        raise ValueError(f"unknown schedule fields: {', '.join(sorted(extra))}")
    if "days" in payload and "at" not in payload:
        raise ValueError("days is only valid together with at")
    if "at" in payload:
        at = str(payload["at"])
        _parse_time(at)
        raw_days = payload.get("days") or []
        if not isinstance(raw_days, (list, tuple)):
            raise ValueError("days must be a list of weekday names")
        days = tuple(dict.fromkeys(str(day).strip().lower() for day in raw_days))
        unknown = [day for day in days if day not in _DAY_NAMES]
        if unknown:
            raise ValueError(f"unknown weekday names: {', '.join(unknown)}")
        return Schedule(at=at, days=days)
    if "every_seconds" in payload:
        try:
            every = float(payload["every_seconds"])
        except (TypeError, ValueError):
            raise ValueError("every_seconds must be a number") from None
        if not _MIN_EVERY_SECONDS <= every <= _MAX_EVERY_SECONDS:
            raise ValueError("every_seconds must be between 60 and 604800")
        return Schedule(every_seconds=every)
    once_raw = str(payload["once_at"])
    try:
        datetime.fromisoformat(once_raw)
    except ValueError:
        raise ValueError("once_at must be an ISO datetime") from None
    return Schedule(once_at=once_raw)


def _parse_time(value: str) -> tuple[int, int]:
    parts = value.split(":")
    if len(parts) != 2:
        raise ValueError("at must be HH:MM")
    try:
        hour, minute = int(parts[0]), int(parts[1])
    except ValueError:
        raise ValueError("at must be HH:MM") from None
    if not (0 <= hour <= 23 and 0 <= minute <= 59):
        raise ValueError("at must be a valid 24h time")
    return hour, minute


def describe_schedule(schedule: Schedule) -> str:
    if schedule.kind == "at":
        if schedule.days:
            return f"{'/'.join(schedule.days)} at {schedule.at}"
        return f"daily at {schedule.at}"
    if schedule.kind == "every":
        seconds = int(schedule.every_seconds or 0)
        if seconds % 3600 == 0:
            return f"every {seconds // 3600}h"
        if seconds % 60 == 0:
            return f"every {seconds // 60}m"
        return f"every {seconds}s"
    return f"once at {schedule.once_at}"


def next_run(schedule: Schedule, now: datetime) -> datetime:
    """The next fire strictly after `now` for at/every. One-shots return their moment
    even when it is already past — a missed reminder fires late, not never."""
    if now.tzinfo is None:
        raise ValueError("now must be timezone-aware")
    if schedule.kind == "once":
        moment = datetime.fromisoformat(schedule.once_at or "")
        if moment.tzinfo is None:
            moment = moment.replace(tzinfo=now.tzinfo)
        return moment
    if schedule.kind == "every":
        return now + timedelta(seconds=float(schedule.every_seconds or 0))
    hour, minute = _parse_time(schedule.at or "")
    candidate = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
    for _ in range(8):
        if candidate > now and (
            not schedule.days or _DAY_NAMES[candidate.weekday()] in schedule.days
        ):
            return candidate
        candidate += timedelta(days=1)
    raise ValueError("no next occurrence within a week")
