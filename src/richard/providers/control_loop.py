from __future__ import annotations

from datetime import datetime, timedelta
from typing import Callable

from richard.control_loops import ControlLoopStore, ControlTargetReader
from richard.schedules import describe_schedule, parse_schedule


_SCHEDULE_PROPERTY = {
    "type": "object",
    "description": (
        "Presence makes this a scheduled loop that wakes Richard at times "
        "instead of on state changes. Exactly one shape: {\"at\": \"HH:MM\", "
        "\"days\": [\"mon\", ...]} daily/weekly, {\"every_minutes\": N} recurring, "
        "or {\"once_in_minutes\": N} / {\"once_at\": \"ISO datetime\"} one-shot. "
        "To verify a condition after a delay (e.g. 'is the door still open in "
        "10 minutes'), create a one-shot scheduled loop. Scheduled loops may "
        "have zero targets (pure reminders)."
    ),
    "properties": {
        "at": {"type": "string"},
        "days": {"type": "array", "items": {"type": "string"}},
        "every_minutes": {"type": "number"},
        "once_in_minutes": {"type": "number"},
        "once_at": {"type": "string"},
    },
    "additionalProperties": False,
}

CREATE_SCHEMA = {
    "type": "function",
    "function": {
        "name": "create_control_loop",
        "description": (
            "Create a persistent background monitor for connected targets (for example Home Assistant entities). "
            "After its first baseline read, a state or attribute change wakes Richard's LLM."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "Short descriptive loop name."},
                "targets": {
                    "type": "array",
                    "items": {"type": "string"},
                    "minItems": 1,
                    "maxItems": 32,
                    "description": (
                        "Target ids as kind:id (for example ha:light.kitchen) or friendly names. "
                        "Required unless a schedule is given."
                    ),
                },
                "trigger_description": {
                    "type": "string",
                    "description": (
                        "Natural-language condition Richard should evaluate after a raw change, "
                        "plus what to do when it applies."
                    ),
                },
                "interval_seconds": {
                    "type": "number",
                    "minimum": 5,
                    "maximum": 86400,
                    "description": "Polling interval in seconds; defaults to 30.",
                },
                "schedule": _SCHEDULE_PROPERTY,
            },
            "required": ["name", "trigger_description"],
        },
    },
}

LIST_SCHEMA = {
    "type": "function",
    "function": {
        "name": "list_control_loops",
        "description": "List persistent control loops and their health.",
        "parameters": {"type": "object", "properties": {}},
    },
}

UPDATE_SCHEMA = {
    "type": "function",
    "function": {
        "name": "update_control_loop",
        "description": "Pause, resume, or edit an existing control loop.",
        "parameters": {
            "type": "object",
            "properties": {
                "loop_id": {"type": "integer"},
                "name": {"type": "string"},
                "targets": {"type": "array", "items": {"type": "string"}},
                "trigger_description": {"type": "string"},
                "interval_seconds": {"type": "number", "minimum": 5, "maximum": 86400},
                "enabled": {"type": "boolean"},
                "schedule": _SCHEDULE_PROPERTY,
            },
            "required": ["loop_id"],
        },
    },
}

DELETE_SCHEMA = {
    "type": "function",
    "function": {
        "name": "delete_control_loop",
        "description": "Permanently delete a control loop by id.",
        "parameters": {
            "type": "object",
            "properties": {"loop_id": {"type": "integer"}},
            "required": ["loop_id"],
        },
    },
}


def _compile_schedule(raw: object, now: datetime) -> dict:
    if not isinstance(raw, dict):
        raise ValueError("schedule must be an object")
    payload = dict(raw)
    if "every_minutes" in payload:
        try:
            payload["every_seconds"] = float(payload.pop("every_minutes")) * 60
        except (TypeError, ValueError):
            raise ValueError("every_minutes must be a number") from None
    if "once_in_minutes" in payload:
        try:
            minutes = float(payload.pop("once_in_minutes"))
        except (TypeError, ValueError):
            raise ValueError("once_in_minutes must be a number") from None
        payload["once_at"] = (now + timedelta(minutes=minutes)).isoformat()
    parse_schedule(payload)  # validate now so the model gets the message immediately
    return payload


def _line(loop) -> str:
    state = "running" if loop.enabled else "paused"
    health = f", error={loop.last_error}" if loop.last_error else ""
    if loop.kind == "scheduled":
        timing = f"{describe_schedule(parse_schedule(loop.schedule))}, next {loop.next_run_at or '—'}"
    else:
        timing = f"every {loop.interval_seconds:g}s"
    return (
        f"[{loop.id}] {loop.name}: {state}, {timing}, "
        f"targets={', '.join(loop.targets)}; trigger={loop.trigger_description!r}{health}"
    )


class ControlLoopProvider:
    def __init__(
        self,
        store: ControlLoopStore,
        reader: ControlTargetReader,
        *,
        now: Callable[[], datetime] | None = None,
    ) -> None:
        self._store = store
        self._reader = reader
        self._now = now or (lambda: datetime.now().astimezone())

    def schemas(self) -> list[dict]:
        return [CREATE_SCHEMA, LIST_SCHEMA, UPDATE_SCHEMA, DELETE_SCHEMA]

    def context(self) -> str | None:
        return (
            "You can create persistent control loops that monitor connected targets "
            "(kind:id, for example ha:light.kitchen) and wake you when state or attributes change. Each "
            "loop has a natural-language trigger description that you evaluate after a change. "
            "Create one only when the user explicitly "
            "asks for ongoing monitoring or automation. The first poll establishes a baseline; "
            "it does not generate a notification. On later changes, you evaluate the trigger; "
            "non-matching changes are recorded as state but do not enter the user's event inbox."
            " Scheduled loops wake Richard at times instead of on change: use them for "
            "time-target checks, reminders, and one-shot re-checks of a condition after "
            "a delay."
        )

    def execute(self, name: str, arguments: dict) -> str:
        try:
            if name == "create_control_loop":
                return self._create(arguments)
            if name == "list_control_loops":
                loops = self._store.all()
                return "\n".join(_line(loop) for loop in loops) if loops else "No control loops."
            if name == "update_control_loop":
                return self._update(arguments)
            if name == "delete_control_loop":
                loop_id = int(arguments.get("loop_id"))
                return (
                    f"Deleted control loop {loop_id}."
                    if self._store.remove(loop_id)
                    else f"No control loop with id {loop_id}."
                )
        except (TypeError, ValueError) as exc:
            return str(exc)
        return f"Unknown tool: {name}."

    def _create(self, arguments: dict) -> str:
        raw_schedule = arguments.get("schedule")
        schedule = _compile_schedule(raw_schedule, self._now()) if raw_schedule is not None else None
        kind = "scheduled" if raw_schedule is not None else "change"
        if kind == "scheduled":
            targets = arguments.get("targets") or []
            if targets:
                targets, error = self._reader.resolve(targets)
                if error:
                    return error
        else:
            targets, error = self._reader.resolve(arguments.get("targets"))
            if error:
                return error
        loop = self._store.create(
            name=str(arguments.get("name", "")),
            targets=targets,
            trigger_description=str(arguments.get("trigger_description", "")),
            interval_seconds=float(arguments.get("interval_seconds", 30.0)),
            kind=kind,
            schedule=schedule,
        )
        return f"Created control loop. {_line(loop)}. Its first poll will establish a baseline."

    def _update(self, arguments: dict) -> str:
        loop_id = int(arguments.get("loop_id"))
        current = self._store.get(loop_id)
        targets = None
        if "targets" in arguments:
            raw_targets = arguments["targets"]
            if current is not None and current.kind == "scheduled" and raw_targets == []:
                targets = []
            else:
                targets, error = self._reader.resolve(raw_targets)
                if error:
                    return error
        raw_schedule = arguments.get("schedule")
        schedule = _compile_schedule(raw_schedule, self._now()) if raw_schedule is not None else None
        loop = self._store.update(
            loop_id,
            name=str(arguments["name"]) if "name" in arguments else None,
            targets=targets,
            trigger_description=(
                str(arguments["trigger_description"])
                if "trigger_description" in arguments
                else None
            ),
            interval_seconds=(
                float(arguments["interval_seconds"])
                if "interval_seconds" in arguments
                else None
            ),
            enabled=bool(arguments["enabled"]) if "enabled" in arguments else None,
            schedule=schedule,
        )
        return _line(loop) if loop is not None else f"No control loop with id {loop_id}."
