"""Discover, build and expose plugins (spec zero).

Discovery reads the `richard.plugins` entry-point group and records name, version
and module without importing anything. `build` imports and builds only the enabled
plugins, in list order, each in its own try/except: a plugin that raises is logged
with its traceback and skipped, so Richard never fails to start because of one.
"""
from __future__ import annotations

import logging
import traceback
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from importlib import metadata
from pathlib import Path

from richard.plugins.base import EventSource, Plugin, PluginContext, PluginParts, TargetReader
from richard.providers.base import Provider

log = logging.getLogger(__name__)

ENTRY_POINT_GROUP = "richard.plugins"


@dataclass
class PluginRecord:
    name: str
    version: str
    module: str
    plugin: Plugin | None = None
    enabled: bool = False
    parts: PluginParts | None = None
    error: str | None = None
    missing: bool = False

    @property
    def status(self) -> str:
        if self.missing:
            return "missing"
        if self.error:
            return "error"
        if self.parts is not None:
            return "built"
        return "enabled" if self.enabled else "disabled"


class PluginRegistry:
    def __init__(
        self,
        plugins: Iterable[Plugin] = (),
        *,
        entry_points: Callable[..., Iterable] | None = None,
    ) -> None:
        self._records: dict[str, PluginRecord] = {}
        self._entry_points: dict[str, object] = {}
        self._finder = entry_points or metadata.entry_points
        self._order: list[str] = []
        for plugin in plugins:
            self._records[plugin.name] = PluginRecord(
                name=plugin.name, version=plugin.version,
                module=type(plugin).__module__, plugin=plugin,
            )

    def discover(self) -> list[PluginRecord]:
        for ep in self._finder(group=ENTRY_POINT_GROUP):
            if ep.name in self._records:
                continue
            dist = getattr(ep, "dist", None)
            version = getattr(dist, "version", None) or "?"
            self._records[ep.name] = PluginRecord(name=ep.name, version=version, module=ep.value)
            self._entry_points[ep.name] = ep
        return self.records()

    def _load(self, record: PluginRecord) -> Plugin:
        if record.plugin is None:
            factory = self._entry_points[record.name].load()
            record.plugin = factory()
        return record.plugin

    def build(
        self,
        enabled: list[str],
        tables: dict[str, dict],
        *,
        persona_name: str,
        data_dir: Path,
        write: Callable[[str], None] = print,
    ) -> None:
        self._order = list(enabled)
        for name in enabled:
            record = self._records.get(name)
            if record is None:
                self._records[name] = PluginRecord(name=name, version="?", module="?", enabled=True, missing=True)
                log.warning("plugin %s is enabled but not installed", name)
                write(f"Plugin {name} is enabled but not installed; skipping.")
                continue
            record.enabled = True
            try:
                plugin = self._load(record)
                config = {**plugin.config_defaults(), **(tables.get(name) or {})}
                ctx = PluginContext(
                    config=config, persona_name=persona_name,
                    data_dir=Path(data_dir) / name, write=write,
                )
                record.parts = plugin.build(ctx)
                record.error = None
            except Exception as exc:
                record.parts = None
                record.error = f"{type(exc).__name__}: {exc}"
                log.error("plugin %s failed to build:\n%s", name, traceback.format_exc())
                write(f"Plugin {name} disabled ({exc})")

    def records(self) -> list[PluginRecord]:
        """Enabled plugins first, in their configured order, then the rest as discovered."""
        ordered = [self._records[name] for name in self._order if name in self._records]
        ordered.extend(record for record in self._records.values() if record.name not in self._order)
        return ordered

    def _built(self) -> list[PluginParts]:
        parts = []
        for name in self._order:
            record = self._records.get(name)
            if record is not None and record.parts is not None:
                parts.append(record.parts)
        return parts

    def providers(self) -> list[Provider]:
        return [p for parts in self._built() for p in parts.providers]

    def target_readers(self) -> dict[str, TargetReader]:
        readers: dict[str, TargetReader] = {}
        for parts in self._built():
            for kind, reader in parts.target_readers.items():
                readers.setdefault(kind, reader)  # first enabled plugin owns a kind
        return readers

    def event_sources(self) -> list[EventSource]:
        return [s for parts in self._built() for s in parts.event_sources]

    def context_lines(self) -> list[str]:
        return [parts.context for parts in self._built() if parts.context]

    def shutdown(self) -> None:
        for parts in self._built():
            if parts.shutdown is not None:
                try:
                    parts.shutdown()
                except Exception:  # a plugin's teardown must not block Richard's
                    log.error("plugin shutdown failed:\n%s", traceback.format_exc())
