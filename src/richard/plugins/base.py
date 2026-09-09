"""The plugin contract (spec zero, 2026-09-08).

A plugin is an installable connection: it contributes tool providers (the existing
Provider protocol), target readers keyed by the kind used in control-loop targets
(`ha:light.kitchen` -> reader "ha"), event sources that push changes instead of
being polled, and one capability line for the system prompt. Disabled plugins are
invisible to the model.
"""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Protocol

from richard.providers.base import Provider
from richard.verification import VerificationResult


@dataclass(frozen=True)
class PluginContext:
    config: dict  # the plugin's [plugins.<name>] table with config_defaults() applied
    persona_name: str
    data_dir: Path  # ~/.richard/plugins/<name>/ for plugin-owned state
    write: Callable[[str], None]


@dataclass(frozen=True)
class TargetInfo:
    id: str
    name: str


@dataclass(frozen=True)
class Event:
    kind: str
    target: str  # full target, e.g. "reachy:face_present"
    payload: dict = field(default_factory=dict)
    observed_at: datetime | None = None


EventSink = Callable[[Event], None]
EventSource = Callable[[EventSink], Callable[[], None]]  # start(sink) -> stop()


class TargetReader(Protocol):
    def read(self, target_id: str) -> dict:
        """Snapshot of one target: {"name": str, "state": str, "attributes": dict}."""
        ...

    def list_targets(self) -> list[TargetInfo]: ...

    def verify(self, target_id: str, expected: dict) -> VerificationResult: ...


@dataclass
class PluginParts:
    providers: list[Provider] = field(default_factory=list)
    target_readers: dict[str, TargetReader] = field(default_factory=dict)  # kind -> reader
    event_sources: list[EventSource] = field(default_factory=list)
    context: str | None = None  # one capability line, appended after the persona
    shutdown: Callable[[], None] | None = None


class Plugin(Protocol):
    name: str  # entry-point name == config table name
    version: str

    def config_defaults(self) -> dict: ...

    def build(self, ctx: PluginContext) -> PluginParts: ...
