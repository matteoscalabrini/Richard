"""Local time for prompts and tools. The system head is pinned for the prompt cache, so
the clock never goes there: callers put `now_line()` into the per-turn, non-persisted
observation slot, and memories render their stored UTC timestamp through `stamp()`."""
from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


def zone_for(tz_name: str | None):
    if tz_name:
        try:
            return ZoneInfo(tz_name)
        except (ZoneInfoNotFoundError, ValueError):
            return None
    return None


def local_now(tz_name: str | None = None) -> datetime:
    zone = zone_for(tz_name)
    return datetime.now(zone) if zone is not None else datetime.now().astimezone()


def now_line(now: datetime) -> str:
    return f"Now: {now:%A %Y-%m-%d %H:%M %Z}"


def stamp(iso_utc: str, tz_name: str | None = None) -> str:
    try:
        moment = datetime.fromisoformat(iso_utc)
    except (TypeError, ValueError):
        return ""
    zone = zone_for(tz_name)
    local = moment.astimezone(zone) if zone is not None else moment.astimezone()
    return local.strftime("%Y-%m-%d %H:%M")
