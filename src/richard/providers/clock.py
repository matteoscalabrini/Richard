"""The time, as a tool: the head is pinned for the cache, so the clock cannot live there."""
from __future__ import annotations

from collections.abc import Callable
from datetime import datetime, tzinfo

from richard.clock import zone_for, local_now

CLOCK_SCHEMA = {"type": "function", "function": {
    "name": "clock",
    "description": ("The current date and time, with weekday and timezone. Use it whenever the user "
                    "asks about the time, the date, the day of the week, or how long until or since something."),
    "parameters": {"type": "object", "properties": {}}}}


class ClockProvider:
    def __init__(self, tz_name: str | None = None, now: Callable[[tzinfo | None], datetime] | None = None) -> None:
        self._tz_name = tz_name
        self._now = now

    def schemas(self) -> list[dict]:
        return [CLOCK_SCHEMA]

    def execute(self, name: str, arguments: dict) -> str:
        if name != "clock":
            return f"Unknown tool: {name}."
        zone = zone_for(self._tz_name)
        moment = self._now(zone) if self._now is not None else local_now(self._tz_name)
        text = moment.strftime("%A %Y-%m-%d %H:%M %Z")
        return f"{text} ({self._tz_name})" if self._tz_name and zone is not None else text

    def context(self) -> str | None:
        return None
