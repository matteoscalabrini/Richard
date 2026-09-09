# Plugin Architecture (spec zero) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Richard's connections become installable plugins discovered through Python entry points; Home Assistant is the first one, moved out of the core, with namespaced control-loop targets and an event-source slot ready for the Reachy body plugin.

**Architecture:** A `PluginRegistry` discovers plugins from the `richard.plugins` entry-point group, builds only the ones listed in `[plugins] enabled`, and hands the core three things per plugin: tool providers (the existing `Provider` protocol), target readers keyed by kind (`ha`, later `reachy`), and event sources. `cli.py` stops hand-assembling provider lists and asks the registry; control loops and diagnostics read every target through a reader looked up by the `kind:` prefix. The Home Assistant client, provider and its verification semantics move into `richard.plugins.home_assistant`; grading stays in `richard.verification`.

**Tech Stack:** Python 3.11+, `importlib.metadata.entry_points(group=...)`, `tomllib`/`tomli-w`, sqlite3 (`PRAGMA user_version`), pytest. No new runtime dependencies.

**Spec:** `docs/superpowers/specs/2026-09-08-plugin-architecture-design.md` (approved 2026-09-07).

## Global Constraints

- Discovery by entry points, group name exactly `richard.plugins`. No directory scanner, no static registry.
- Loop targets are `kind:id` and the kind is mandatory. Rows whose targets lack a kind are deleted at migration with one log line. No compatibility shim.
- `[plugins.<name>]` tables are preserved by `save_config` even when Richard does not model them.
- A plugin that raises in `build` is logged with its traceback and skipped; Richard never fails to start because of a plugin.
- Voice engines, brains and memory are not plugins.
- The private device integration is a separate private package; nothing in this repo references it. Before any push to the `public` remote, run the repo's private-string check (the pattern is in the private memory `matteo-working-style`, never in this tree) and it must print nothing.
- Every change is TDD: failing test first, minimal code, suite green, commit. Suite command: `.venv/bin/python -m pytest -q` from the worktree root `/Users/matteo/Documents/GitHub/Richard/.worktrees/reachy-presence`, branch `reachy-presence`. Baseline: 518 passed, 2 skipped at `da851a5`.
- Commit messages: imperative subject, a body that says why, and the trailer `Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>`.

---

## File structure

**Create**
- `src/richard/plugins/__init__.py` — empty package marker.
- `src/richard/plugins/base.py` — the contract: `PluginContext`, `PluginParts`, `Plugin`, `TargetReader`, `TargetInfo`, `Event`, `EventSource`.
- `src/richard/plugins/registry.py` — `PluginRegistry`, `PluginRecord`, `ENTRY_POINT_GROUP`.
- `src/richard/plugins/home_assistant/__init__.py` — `HomeAssistantPlugin`.
- `src/richard/plugins/home_assistant/client.py` — moved from `src/richard/home_assistant.py` (unchanged content).
- `src/richard/plugins/home_assistant/provider.py` — moved from `src/richard/providers/home_assistant.py` (imports updated).
- `src/richard/plugins/home_assistant/reader.py` — `HomeAssistantTargetReader` (read, list_targets, verify).
- `tests/fake_plugin.py` — a `FakePlugin` importable by a real `importlib.metadata.EntryPoint`.
- `tests/test_plugins_base.py`, `tests/test_plugins_registry.py`, `tests/test_plugin_home_assistant.py`, `tests/test_cli_plugins.py`.
- `docs/plugins.md`.

**Modify**
- `src/richard/config.py` — `Plugins` dataclass on `Config`, `[plugins]` load/save, legacy `[home_assistant]` migration, `RICHARD_HA_*` mapped onto the plugin table, `HomeAssistant.from_table/to_table`, `Config.home_assistant` becomes a read-only view.
- `src/richard/verification.py` — `judge()` and `failed()` move here from `diagnostics.py`.
- `src/richard/diagnostics.py` — reads through `dict[str, TargetReader]`; `RefreshReport` generalized per source.
- `src/richard/control_loops.py` — `ControlTargetReader(readers)`, no Home Assistant import, `PRAGMA user_version` 2 migration, pushed events in the monitor.
- `src/richard/cli.py` — registry-driven assembly, `richard plugins` subcommand, `--set-ha-*` writing the plugin table.
- `src/richard/web/app.py` — Home Assistant config routes read and write the plugin table.
- `pyproject.toml` — `[project.entry-points."richard.plugins"]`, `slow` marker.
- `README.md` — one paragraph.
- Tests touched by the moves: `tests/test_home_assistant_client.py`, `tests/test_provider_home_assistant.py`, `tests/test_control_loops.py`, `tests/test_diagnostics.py`, `tests/test_config.py`, `tests/test_cli.py`, `tests/test_web.py`.

**Delete** (at the end of the task that moves them): `src/richard/home_assistant.py`, `src/richard/providers/home_assistant.py`.

Task order keeps the suite green after every task: contracts → registry → config tables → Home Assistant plugin package (registered, not yet wired) → loops by kind → diagnostics by kind → config migration and views → cli assembly through the registry → `richard plugins` CLI → event sources → docs.

---

### Task 1: Plugin contracts

**Files:**
- Create: `src/richard/plugins/__init__.py`, `src/richard/plugins/base.py`
- Test: `tests/test_plugins_base.py`

**Interfaces:**
- Produces: `PluginContext(config: dict, persona_name: str, data_dir: Path, write: Callable[[str], None])`; `TargetInfo(id: str, name: str)`; `Event(kind: str, target: str, payload: dict = {}, observed_at: datetime | None = None)`; `EventSink = Callable[[Event], None]`; `EventSource = Callable[[EventSink], Callable[[], None]]`; `TargetReader` protocol with `read(target_id) -> dict`, `list_targets() -> list[TargetInfo]`, `verify(target_id, expected) -> VerificationResult`; `PluginParts(providers=[], target_readers={}, event_sources=[], context=None, shutdown=None)`; `Plugin` protocol with `name`, `version`, `config_defaults() -> dict`, `build(ctx) -> PluginParts`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_plugins_base.py
from pathlib import Path

from richard.plugins.base import (
    Event,
    Plugin,
    PluginContext,
    PluginParts,
    TargetInfo,
    TargetReader,
)
from richard.verification import VerificationResult, VerificationStatus


class FakeReader:
    def read(self, target_id):
        return {"name": target_id, "state": "on", "attributes": {}}

    def list_targets(self):
        return [TargetInfo(id="light.one", name="Lamp")]

    def verify(self, target_id, expected):
        return VerificationResult(
            status=VerificationStatus.CONFIRMED, source="fake", target=target_id,
            name="Lamp", action="set", requested=expected, observed=expected,
        )


class FakePlugin:
    name = "fake"
    version = "0.1"

    def config_defaults(self):
        return {"greeting": "hi"}

    def build(self, ctx: PluginContext) -> PluginParts:
        return PluginParts(target_readers={"fake": FakeReader()}, context=f"Fake says {ctx.config['greeting']}")


def test_plugin_parts_defaults_are_empty():
    parts = PluginParts()
    assert parts.providers == []
    assert parts.target_readers == {}
    assert parts.event_sources == []
    assert parts.context is None
    assert parts.shutdown is None


def test_fake_plugin_satisfies_the_contract(tmp_path):
    plugin: Plugin = FakePlugin()
    ctx = PluginContext(config={"greeting": "ciao"}, persona_name="Richard", data_dir=tmp_path, write=lambda s: None)
    parts = plugin.build(ctx)
    reader: TargetReader = parts.target_readers["fake"]
    assert reader.read("light.one")["state"] == "on"
    assert parts.context == "Fake says ciao"


def test_event_defaults():
    event = Event(kind="reachy", target="reachy:face_present")
    assert event.payload == {}
    assert event.observed_at is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_plugins_base.py -q`
Expected: FAIL with `ModuleNotFoundError: No module named 'richard.plugins'`

- [ ] **Step 3: Write minimal implementation**

`src/richard/plugins/__init__.py`: empty file.

```python
# src/richard/plugins/base.py
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
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_plugins_base.py -q`
Expected: 3 passed

- [ ] **Step 5: Commit**

```bash
git add src/richard/plugins/__init__.py src/richard/plugins/base.py tests/test_plugins_base.py
git commit -m "plugins: the contract (PluginContext, PluginParts, TargetReader, Event)

Spec zero's first step: the interfaces the registry and the first plugin build on.
No behaviour change yet.

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 2: PluginRegistry (discovery, build isolation, accessors)

**Files:**
- Create: `src/richard/plugins/registry.py`, `tests/fake_plugin.py`
- Test: `tests/test_plugins_registry.py`

**Interfaces:**
- Consumes: Task 1 contracts.
- Produces: `ENTRY_POINT_GROUP = "richard.plugins"`; `PluginRecord(name, version, module, plugin=None, enabled=False, parts=None, error=None)` with `.status -> "error" | "built" | "enabled" | "disabled" | "missing"`; `PluginRegistry(plugins: Iterable[Plugin] = (), *, entry_points: Callable | None = None)` with `discover() -> list[PluginRecord]`, `build(enabled: list[str], tables: dict[str, dict], *, persona_name: str, data_dir: Path, write=print) -> None`, `providers() -> list[Provider]`, `target_readers() -> dict[str, TargetReader]`, `event_sources() -> list[EventSource]`, `context_lines() -> list[str]`, `records() -> list[PluginRecord]`, `shutdown() -> None`.

- [ ] **Step 1: Write the failing tests**

```python
# tests/fake_plugin.py
"""A plugin that a real importlib.metadata.EntryPoint can load in tests."""
from richard.plugins.base import PluginParts


class FakePlugin:
    name = "fake"
    version = "9.9"

    def config_defaults(self):
        return {"greeting": "hi", "retries": 1}

    def build(self, ctx):
        return PluginParts(context=f"Fake ({ctx.config['greeting']}, retries {ctx.config['retries']})")
```

```python
# tests/test_plugins_registry.py
import logging
from importlib import metadata
from types import SimpleNamespace

import pytest

from richard.plugins.base import PluginParts, TargetInfo
from richard.plugins.registry import ENTRY_POINT_GROUP, PluginRegistry


class Reader:
    def read(self, target_id):
        return {"name": target_id, "state": "x", "attributes": {}}

    def list_targets(self):
        return [TargetInfo("a", "A")]

    def verify(self, target_id, expected):
        raise NotImplementedError


class Good:
    name = "good"
    version = "1.0"

    def __init__(self):
        self.stopped = False
        self.ctx = None

    def config_defaults(self):
        return {"port": 1, "host": "default.local"}

    def build(self, ctx):
        self.ctx = ctx
        source_stop = lambda: None
        return PluginParts(
            providers=["good-provider"],
            target_readers={"good": Reader()},
            event_sources=[lambda sink: source_stop],
            context="Good is connected.",
            shutdown=lambda: setattr(self, "stopped", True),
        )


class Broken:
    name = "broken"
    version = "1.0"

    def config_defaults(self):
        return {}

    def build(self, ctx):
        raise RuntimeError("boom")


def _build(registry, enabled, tables=None, tmp_path=None, write=None):
    registry.build(enabled, tables or {}, persona_name="Richard", data_dir=tmp_path, write=write or (lambda s: None))


def test_build_only_enabled_in_order(tmp_path):
    a, b = Good(), Good()
    b.name = "second"
    registry = PluginRegistry([b, a])
    _build(registry, ["good", "second"], tmp_path=tmp_path)
    assert registry.providers() == ["good-provider", "good-provider"]
    assert [r.name for r in registry.records() if r.status == "built"] == ["good", "second"]
    assert list(registry.target_readers()) == ["good"]  # same kind twice: first wins
    assert registry.context_lines() == ["Good is connected.", "Good is connected."]
    assert len(registry.event_sources()) == 2


def test_disabled_plugin_contributes_nothing(tmp_path):
    registry = PluginRegistry([Good()])
    _build(registry, [], tmp_path=tmp_path)
    assert registry.providers() == []
    assert registry.context_lines() == []
    assert registry.records()[0].status == "disabled"


def test_config_defaults_are_applied_under_the_table(tmp_path):
    plugin = Good()
    registry = PluginRegistry([plugin])
    _build(registry, ["good"], {"good": {"port": 8123}}, tmp_path=tmp_path)
    assert plugin.ctx.config == {"port": 8123, "host": "default.local"}
    assert plugin.ctx.persona_name == "Richard"
    assert plugin.ctx.data_dir == tmp_path / "good"


def test_build_failure_is_isolated_and_logged(tmp_path, caplog):
    lines = []
    registry = PluginRegistry([Broken(), Good()])
    with caplog.at_level(logging.ERROR, logger="richard.plugins.registry"):
        _build(registry, ["broken", "good"], tmp_path=tmp_path, write=lines.append)
    assert registry.providers() == ["good-provider"]
    broken = next(r for r in registry.records() if r.name == "broken")
    assert broken.status == "error"
    assert broken.error == "RuntimeError: boom"
    assert "Traceback" in caplog.text
    assert lines == ["Plugin broken disabled (boom)"]


def test_enabled_but_not_installed_is_reported(tmp_path):
    lines = []
    registry = PluginRegistry([])
    _build(registry, ["ghost"], tmp_path=tmp_path, write=lines.append)
    assert lines == ["Plugin ghost is enabled but not installed; skipping."]
    assert [r.status for r in registry.records()] == ["missing"]


def test_shutdown_calls_each_built_plugin(tmp_path):
    plugin = Good()
    registry = PluginRegistry([plugin])
    _build(registry, ["good"], tmp_path=tmp_path)
    registry.shutdown()
    assert plugin.stopped


def test_discover_reads_entry_points_lazily(tmp_path):
    loaded = []

    class EP:
        name = "lazy"
        value = "tests.fake_plugin:FakePlugin"
        dist = SimpleNamespace(version="3.2.1")

        def load(self):
            loaded.append(True)
            from tests.fake_plugin import FakePlugin
            return FakePlugin

    def finder(*, group):
        assert group == ENTRY_POINT_GROUP
        return [EP()]

    registry = PluginRegistry(entry_points=finder)
    records = registry.discover()
    assert [(r.name, r.version, r.module) for r in records] == [("lazy", "3.2.1", "tests.fake_plugin:FakePlugin")]
    assert loaded == []  # discovery does not import
    _build(registry, ["lazy"], {"lazy": {"greeting": "ciao"}}, tmp_path=tmp_path)
    assert loaded == [True]
    assert registry.context_lines() == ["Fake (ciao, retries 1)"]


def test_discover_through_a_real_entry_point(tmp_path):
    ep = metadata.EntryPoint(name="fake", value="tests.fake_plugin:FakePlugin", group=ENTRY_POINT_GROUP)
    registry = PluginRegistry(entry_points=lambda *, group: [ep])
    registry.discover()
    _build(registry, ["fake"], tmp_path=tmp_path)
    assert registry.context_lines() == ["Fake (hi, retries 1)"]


def test_discover_skips_a_name_already_registered_in_memory(tmp_path):
    ep = metadata.EntryPoint(name="good", value="tests.fake_plugin:FakePlugin", group=ENTRY_POINT_GROUP)
    registry = PluginRegistry([Good()], entry_points=lambda *, group: [ep])
    assert [r.module for r in registry.discover()] == [Good.__module__]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_plugins_registry.py -q`
Expected: FAIL with `ModuleNotFoundError: No module named 'richard.plugins.registry'`

- [ ] **Step 3: Write minimal implementation**

```python
# src/richard/plugins/registry.py
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
        return list(self._records.values())

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
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_plugins_registry.py tests/test_plugins_base.py -q`
Expected: 12 passed

- [ ] **Step 5: Commit**

```bash
git add src/richard/plugins/registry.py tests/fake_plugin.py tests/test_plugins_registry.py
git commit -m "plugins: registry with lazy entry-point discovery and isolated builds

Discovery never imports; build imports and builds enabled plugins in list order,
each in a try/except that logs the traceback and skips, so a broken plugin cannot
stop Richard. Accessors give the core providers, readers by kind, event sources
and context lines.

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 3: `[plugins]` config tables with round-trip

**Files:**
- Modify: `src/richard/config.py` (Config dataclass at lines 128-141; `load_config` 173-307; `save_config` 310-409)
- Test: `tests/test_config.py`

**Interfaces:**
- Produces: `Plugins(enabled: list[str], tables: dict[str, dict])` with `table(name) -> dict` (creates on demand); `Config.plugins: Plugins`; `default_plugins_dir() -> Path` (`~/.richard/plugins`). `[plugins] enabled = [...]` and `[plugins.<name>]` sub-tables round-trip through `save_config`, unknown names included.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_config.py`:

```python
def test_plugins_table_round_trips_unknown_names(tmp_path):
    path = tmp_path / "config.toml"
    path.write_text(
        '[plugins]\nenabled = ["home_assistant", "mystery"]\n\n'
        '[plugins.home_assistant]\nhost = "ha.local"\nport = 8123\n\n'
        '[plugins.mystery]\nlevel = 3\nflag = true\n'
    )
    config = load_config(path)
    assert config.plugins.enabled == ["home_assistant", "mystery"]
    assert config.plugins.tables["mystery"] == {"level": 3, "flag": True}
    save_config(config, path)
    again = load_config(path)
    assert again.plugins.enabled == ["home_assistant", "mystery"]
    assert again.plugins.tables == {"home_assistant": {"host": "ha.local", "port": 8123}, "mystery": {"level": 3, "flag": True}}


def test_plugins_default_empty_and_table_creates_on_demand():
    config = Config()
    assert config.plugins.enabled == []
    assert config.plugins.tables == {}
    config.plugins.table("reachy")["host"] = "10.99.77.5"
    assert config.plugins.tables == {"reachy": {"host": "10.99.77.5"}}


def test_default_plugins_dir(monkeypatch, tmp_path):
    monkeypatch.setenv("HOME", str(tmp_path))
    assert default_plugins_dir() == tmp_path / ".richard" / "plugins"
```

Add `default_plugins_dir` to the existing import line at the top of `tests/test_config.py` (it imports `Config, load_config, save_config` from `richard.config`).

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_config.py -q`
Expected: FAIL with `ImportError: cannot import name 'default_plugins_dir'`

- [ ] **Step 3: Write minimal implementation**

In `src/richard/config.py`, after the `Realtime` dataclass (line 125) add:

```python
@dataclass
class Plugins:
    """[plugins] enabled = [...] plus one [plugins.<name>] table per plugin.

    Tables are kept verbatim, including ones Richard does not model, so
    save_config never drops a plugin's settings."""

    enabled: list[str] = field(default_factory=list)
    tables: dict[str, dict] = field(default_factory=dict)

    def table(self, name: str) -> dict:
        return self.tables.setdefault(name, {})
```

Add the field to `Config` (after `brains`):

```python
    plugins: Plugins = field(default_factory=Plugins)
```

After `default_config_path()` (line 169-170) add:

```python
def default_plugins_dir() -> Path:
    return Path.home() / ".richard" / "plugins"
```

In `load_config`, before `config = Config(` (line 258) add:

```python
    plugins_data = data.get("plugins") or {}
    plugins = Plugins(
        enabled=[str(name) for name in (plugins_data.get("enabled") or [])],
        tables={
            str(name): dict(table)
            for name, table in plugins_data.items()
            if isinstance(table, dict)
        },
    )
```

and pass `plugins=plugins` into the `Config(...)` call.

In `save_config`, before the `[brains]` block (line 390) add:

```python
    plugins_table: dict = {"enabled": list(config.plugins.enabled)}
    for name, table in config.plugins.tables.items():
        plugins_table[name] = {key: value for key, value in table.items() if value is not None}
    data["plugins"] = plugins_table
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_config.py -q`
Expected: all passed (previous count + 3)

- [ ] **Step 5: Run the full suite, then commit**

Run: `.venv/bin/python -m pytest -q`
Expected: 533 passed, 2 skipped (518 + 3 + 9 + 3)

```bash
git add src/richard/config.py tests/test_config.py
git commit -m "config: [plugins] enabled list and per-plugin tables that round-trip

save_config rebuilds the file from the dataclass, so plugin tables live on the
Config as raw dicts and are written back verbatim, unknown names included.

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 4: Home Assistant plugin package (moved modules, reader, entry point)

**Files:**
- Move: `src/richard/home_assistant.py` → `src/richard/plugins/home_assistant/client.py`; `src/richard/providers/home_assistant.py` → `src/richard/plugins/home_assistant/provider.py`
- Create: `src/richard/plugins/home_assistant/__init__.py`, `src/richard/plugins/home_assistant/reader.py`
- Modify: `src/richard/verification.py` (add `judge`, `failed`), `src/richard/diagnostics.py` (lines 24-25 `_key`, 200-262 `_diagnose_entity`, `_verify_entity`, `_judge`, `_failed`), `src/richard/control_loops.py:12`, `src/richard/web/app.py:37`, `src/richard/cli.py:406-407`, `pyproject.toml`
- Test: `tests/test_plugin_home_assistant.py`; import paths in `tests/test_home_assistant_client.py`, `tests/test_provider_home_assistant.py`, `tests/test_diagnostics.py`, `tests/test_control_loops.py`, `tests/test_web.py`, `tests/test_cli.py`

**Interfaces:**
- Consumes: Task 1 contracts; `richard.config.HomeAssistant` (dataclass at config.py:69-87) and `apply_home_assistant_url`.
- Produces: `richard.plugins.home_assistant.client.HomeAssistantClient`, `HomeAssistantEntity` (unchanged API); `richard.plugins.home_assistant.provider.HomeAssistantProvider`, `entity_snapshot` (unchanged API); `HomeAssistantTargetReader(client)` with `read(entity_id) -> {"name","state","attributes"}`, `list_targets() -> [TargetInfo(entity_id, name)]`, `verify(entity_id, expected) -> VerificationResult`; `HomeAssistantPlugin` (name `home_assistant`, kind `ha`); `richard.verification.judge(build, expected, observed, name) -> VerificationResult` and `failed(target, reason) -> VerificationResult`; `richard.config.HomeAssistant.from_table(table: dict, *, enabled: bool = False) -> HomeAssistant` and `.to_table() -> dict`.

- [ ] **Step 1: Move the two modules and fix imports (mechanical, suite must stay green)**

```bash
mkdir -p src/richard/plugins/home_assistant
git mv src/richard/home_assistant.py src/richard/plugins/home_assistant/client.py
git mv src/richard/providers/home_assistant.py src/richard/plugins/home_assistant/provider.py
grep -rl "richard.home_assistant\b\|richard\.home_assistant import\|richard.providers.home_assistant" src tests
```

Replace, in every file the grep lists (expected: `src/richard/plugins/home_assistant/provider.py`, `src/richard/control_loops.py`, `src/richard/diagnostics.py`, `src/richard/web/app.py`, `src/richard/cli.py`, and the six test files):
- `from richard.home_assistant import` → `from richard.plugins.home_assistant.client import`
- `from richard.providers.home_assistant import` → `from richard.plugins.home_assistant.provider import`

Run: `.venv/bin/python -m pytest -q`
Expected: 533 passed, 2 skipped (nothing else changed)

- [ ] **Step 2: Write the failing tests for the reader and the plugin**

```python
# tests/test_plugin_home_assistant.py
from pathlib import Path

import pytest

from richard.errors import HomeAssistantError
from richard.plugins.base import PluginContext, TargetInfo
from richard.plugins.home_assistant import HomeAssistantPlugin
from richard.plugins.home_assistant.client import HomeAssistantEntity
from richard.plugins.home_assistant.reader import HomeAssistantTargetReader
from richard.verification import VerificationStatus


class FakeClient:
    def __init__(self, entities=None, error=None):
        self.entities = entities if entities is not None else [
            HomeAssistantEntity("light.kitchen", "on", {"friendly_name": "Kitchen Light", "brightness": 200, "icon": "mdi:x"}),
            HomeAssistantEntity("lock.front", "locked", {"friendly_name": "Front Door"}),
        ]
        self.error = error

    def list_entities(self):
        if self.error:
            raise HomeAssistantError(self.error)
        return list(self.entities)

    def get_entity(self, entity_id):
        if self.error:
            raise HomeAssistantError(self.error)
        for entity in self.entities:
            if entity.entity_id == entity_id:
                return entity
        raise HomeAssistantError(f"{entity_id} was not found")


def test_reader_read_uses_the_control_loop_snapshot_shape():
    reader = HomeAssistantTargetReader(FakeClient())
    assert reader.read("light.kitchen") == {
        "name": "Kitchen Light", "state": "on", "attributes": {"friendly_name": "Kitchen Light", "brightness": 200},
    }


def test_reader_lists_targets_by_entity_id_and_name():
    reader = HomeAssistantTargetReader(FakeClient())
    assert reader.list_targets() == [TargetInfo("light.kitchen", "Kitchen Light"), TargetInfo("lock.front", "Front Door")]


def test_reader_verify_confirms_and_mismatches():
    reader = HomeAssistantTargetReader(FakeClient())
    ok = reader.verify("light.kitchen", {"state": "on", "brightness": 200})
    assert ok.status is VerificationStatus.CONFIRMED
    assert ok.source == "home_assistant"
    bad = reader.verify("light.kitchen", {"state": "off"})
    assert bad.status is VerificationStatus.MISMATCH
    assert "state" in bad.reason


def test_reader_verify_failed_when_unreachable():
    reader = HomeAssistantTargetReader(FakeClient(error="Home Assistant is unreachable"))
    result = reader.verify("light.kitchen", {"state": "on"})
    assert result.status is VerificationStatus.FAILED
    assert "unreachable" in result.reason


def test_plugin_defaults_match_the_config_dataclass():
    plugin = HomeAssistantPlugin()
    assert plugin.name == "home_assistant"
    assert plugin.config_defaults() == {
        "host": "homeassistant.local", "port": 8123, "use_https": False,
        "timeout": 10.0, "verify_ssl": True,
    }


def test_plugin_build_needs_host_and_token(tmp_path):
    plugin = HomeAssistantPlugin()
    ctx = PluginContext(config={**plugin.config_defaults(), "host": ""}, persona_name="R", data_dir=tmp_path, write=lambda s: None)
    with pytest.raises(ValueError, match="host or token"):
        plugin.build(ctx)


def test_plugin_build_yields_provider_reader_and_context(tmp_path):
    plugin = HomeAssistantPlugin(client_factory=lambda url, token, **kw: FakeClient())
    ctx = PluginContext(
        config={**plugin.config_defaults(), "host": "ha.local", "token": "t"},
        persona_name="R", data_dir=tmp_path, write=lambda s: None,
    )
    parts = plugin.build(ctx)
    assert [type(p).__name__ for p in parts.providers] == ["HomeAssistantProvider"]
    assert list(parts.target_readers) == ["ha"]
    assert parts.target_readers["ha"].read("lock.front")["state"] == "locked"
    assert parts.context == "Home Assistant is connected at http://ha.local:8123."
    assert parts.event_sources == []


def test_plugin_passes_settings_to_the_client(tmp_path):
    seen = {}

    def factory(url, token, **kwargs):
        seen.update(url=url, token=token, **kwargs)
        return FakeClient()

    plugin = HomeAssistantPlugin(client_factory=factory)
    ctx = PluginContext(
        config={"host": "ha.local", "port": 9443, "use_https": True, "token": "abc", "timeout": 3.0, "verify_ssl": False},
        persona_name="R", data_dir=tmp_path, write=lambda s: None,
    )
    plugin.build(ctx)
    assert seen == {"url": "https://ha.local:9443", "token": "abc", "timeout": 3.0, "verify_ssl": False}


def test_entry_point_is_registered():
    from importlib import metadata

    names = {ep.name: ep.value for ep in metadata.entry_points(group="richard.plugins")}
    assert names["home_assistant"] == "richard.plugins.home_assistant:HomeAssistantPlugin"
```

Also append to `tests/test_config.py`:

```python
def test_home_assistant_from_table_and_to_table():
    from richard.config import HomeAssistant

    settings = HomeAssistant.from_table({"host": "ha.local", "port": 9443, "use_https": True, "token": "t"}, enabled=True)
    assert settings.enabled is True
    assert settings.url == "https://ha.local:9443"
    assert settings.timeout == 10.0  # default kept
    assert settings.to_table() == {
        "host": "ha.local", "port": 9443, "use_https": True, "token": "t", "timeout": 10.0, "verify_ssl": True,
    }
    assert HomeAssistant.from_table({}).to_table() == {
        "host": "homeassistant.local", "port": 8123, "use_https": False, "timeout": 10.0, "verify_ssl": True,
    }
```

- [ ] **Step 3: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_plugin_home_assistant.py tests/test_config.py -q`
Expected: FAIL with `ModuleNotFoundError: No module named 'richard.plugins.home_assistant.reader'` and `AttributeError: type object 'HomeAssistant' has no attribute 'from_table'`

- [ ] **Step 4: Move the grading helpers into verification.py**

Cut `_judge` (diagnostics.py 246-262) and `_failed` (264-268) out of `src/richard/diagnostics.py` and append them to `src/richard/verification.py` as public functions, unchanged in body:

```python
def judge(build, expected: dict, observed: dict, name: str) -> VerificationResult:
    """Grade an observed snapshot against the expected fields. `build(status, reason)`
    produces the VerificationResult so the caller owns source/target/action."""
    mismatched, missing = compare_fields(expected, observed, {key: key for key in expected})
    if mismatched:
        return build(VerificationStatus.MISMATCH, "mismatch on " + ", ".join(mismatched))
    if missing:
        return build(VerificationStatus.UNCONFIRMED, "no reading for " + ", ".join(missing))
    return build(VerificationStatus.CONFIRMED, None)


def failed(source: str, target: str, name: str, action: str, reason: str) -> VerificationResult:
    return VerificationResult(
        status=VerificationStatus.FAILED, source=source, target=target, name=name,
        action=action, reason=reason,
    )
```

(Keep the exact grading logic of the existing `_judge`; the signature above is the one the reader and diagnostics use. If the existing body differs in field-map handling, keep its behaviour and only rename.) In `diagnostics.py` replace the two call sites with `from richard.verification import judge, failed` for now; Task 6 rewrites that module.

- [ ] **Step 5: Add from_table/to_table to `config.HomeAssistant`**

In `src/richard/config.py`, inside the `HomeAssistant` dataclass (after the `url` property):

```python
    @classmethod
    def from_table(cls, table: dict, *, enabled: bool = False) -> "HomeAssistant":
        settings = cls(enabled=enabled)
        if "host" in table:
            settings.host = str(table["host"])
        if "port" in table:
            settings.port = int(table["port"])
        if "use_https" in table:
            settings.use_https = bool(table["use_https"])
        if table.get("token") is not None:
            settings.token = str(table["token"])
        if "timeout" in table:
            settings.timeout = float(table["timeout"])
        if "verify_ssl" in table:
            settings.verify_ssl = bool(table["verify_ssl"])
        if "host" not in table and table.get("url"):
            apply_home_assistant_url(settings, str(table["url"]))
        return settings

    def to_table(self) -> dict:
        table: dict = {
            "host": self.host, "port": self.port, "use_https": self.use_https,
            "timeout": self.timeout, "verify_ssl": self.verify_ssl,
        }
        if self.token is not None:
            table["token"] = self.token
        return table
```

`apply_home_assistant_url` is defined after the class in the same module; Python resolves it at call time, so the forward reference is fine.

- [ ] **Step 6: Write the reader and the plugin**

```python
# src/richard/plugins/home_assistant/reader.py
"""Home Assistant as a control-loop / diagnostics target source (kind "ha")."""
from __future__ import annotations

from richard.errors import HomeAssistantError
from richard.plugins.base import TargetInfo
from richard.plugins.home_assistant.client import HomeAssistantClient, HomeAssistantEntity
from richard.plugins.home_assistant.provider import entity_snapshot
from richard.verification import HOME_ASSISTANT, VerificationResult, failed, judge

_IGNORED_ATTRIBUTES = {"attribution", "entity_picture", "icon", "supported_features"}


def snapshot(entity: HomeAssistantEntity) -> dict:
    attributes = {k: v for k, v in entity.attributes.items() if k not in _IGNORED_ATTRIBUTES}
    return {"name": entity.name, "state": entity.state, "attributes": attributes}


class HomeAssistantTargetReader:
    def __init__(self, client: HomeAssistantClient) -> None:
        self._client = client

    def read(self, target_id: str) -> dict:
        return snapshot(self._client.get_entity(target_id))

    def list_targets(self) -> list[TargetInfo]:
        return [TargetInfo(id=e.entity_id, name=e.name) for e in self._client.list_entities()]

    def verify(self, target_id: str, expected: dict) -> VerificationResult:
        try:
            entity = self._client.get_entity(target_id)
        except HomeAssistantError as exc:
            return failed(HOME_ASSISTANT, f"ha:{target_id}", target_id, "verify", str(exc))
        observed = entity_snapshot(entity)

        def build(status, reason):
            return VerificationResult(
                status=status, source=HOME_ASSISTANT, target=f"ha:{target_id}", name=entity.name,
                action="verify", requested=dict(expected), observed=observed, reason=reason,
            )

        return judge(build, expected, observed, entity.name)
```

```python
# src/richard/plugins/home_assistant/__init__.py
"""Home Assistant as a Richard plugin: tools, the "ha" target kind, one context line."""
from __future__ import annotations

from collections.abc import Callable

from richard.config import HomeAssistant
from richard.plugins.base import PluginContext, PluginParts
from richard.plugins.home_assistant.client import HomeAssistantClient
from richard.plugins.home_assistant.provider import HomeAssistantProvider
from richard.plugins.home_assistant.reader import HomeAssistantTargetReader

KIND = "ha"


class HomeAssistantPlugin:
    name = "home_assistant"
    version = "1.0"

    def __init__(self, client_factory: Callable[..., HomeAssistantClient] = HomeAssistantClient) -> None:
        self._client_factory = client_factory

    def config_defaults(self) -> dict:
        return HomeAssistant().to_table()

    def build(self, ctx: PluginContext) -> PluginParts:
        settings = HomeAssistant.from_table(ctx.config, enabled=True)
        if not settings.host or not settings.token:
            raise ValueError("host or token unset; configure both before restarting Richard")
        client = self._client_factory(
            settings.url, settings.token, timeout=settings.timeout, verify_ssl=settings.verify_ssl,
        )
        return PluginParts(
            providers=[HomeAssistantProvider(client)],
            target_readers={KIND: HomeAssistantTargetReader(client)},
            context=f"Home Assistant is connected at {settings.url}.",
        )
```

Register the entry point in `pyproject.toml` (after `[project.scripts]`):

```toml
[project.entry-points."richard.plugins"]
home_assistant = "richard.plugins.home_assistant:HomeAssistantPlugin"
```

Then refresh the editable install so the entry point exists: `.venv/bin/pip install -e . -q`.

- [ ] **Step 7: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_plugin_home_assistant.py tests/test_config.py tests/test_diagnostics.py tests/test_verification.py -q`
Expected: all passed

- [ ] **Step 8: Full suite, then commit**

Run: `.venv/bin/python -m pytest -q`
Expected: 543 passed, 2 skipped

```bash
git add -A src/richard/plugins src/richard/verification.py src/richard/diagnostics.py src/richard/config.py src/richard/control_loops.py src/richard/web/app.py src/richard/cli.py pyproject.toml tests
git commit -m "plugins: Home Assistant as richard.plugins.home_assistant, registered by entry point

Client and provider move under the plugin package unchanged; a TargetReader for
kind \"ha\" carries the control-loop snapshot shape and the read-back verification
that lived in diagnostics; grading (judge/failed) moves to richard.verification.
Not wired into the runtime yet.

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 5: Control loops read targets by kind; schema bump

**Files:**
- Modify: `src/richard/control_loops.py` (imports line 12; `ControlLoopStore.__init__` 111-166; `_entity_snapshot` 494-499; `ControlTargetReader` 502-560)
- Modify: `src/richard/providers/control_loop.py` (only if it names Home Assistant in text; check lines 200-235)
- Test: `tests/test_control_loops.py` (FakeHomeAssistant at 12-37, `_reader` helper, migration test at 295-325)

**Interfaces:**
- Consumes: `richard.plugins.base.TargetReader`, `TargetInfo`.
- Produces: `ControlTargetReader(readers: dict[str, TargetReader] | None = None)` with the same `resolve(targets) -> tuple[list[str], str | None]` and `read(target) -> dict`; `SCHEMA_VERSION = 2`; kindless rows deleted at open with one log line `control loops: removed N loop(s) with kindless targets`.

- [ ] **Step 1: Rewrite the test fakes and add the new tests**

In `tests/test_control_loops.py` replace `FakeHomeAssistant` (lines 12-37) and `_reader()` with:

```python
from richard.plugins.base import TargetInfo


class FakeReader:
    """A TargetReader for kind "ha" backed by a dict; the same shape a plugin returns."""

    def __init__(self):
        self.snapshots = {
            "light.workbench": {"name": "Workbench Lamp", "state": "off", "attributes": {"brightness": 0}},
            "sensor.temperature": {"name": "Shop Temperature", "state": "20", "attributes": {"unit_of_measurement": "°C"}},
            "binary_sensor.front_door": {"name": "Front Door", "state": "off", "attributes": {}},
        }

    def read(self, target_id):
        try:
            return dict(self.snapshots[target_id])
        except KeyError:
            raise RuntimeError(f"unknown entity: {target_id}") from None

    def list_targets(self):
        return [TargetInfo(id=key, name=value["name"]) for key, value in self.snapshots.items()]

    def verify(self, target_id, expected):
        raise NotImplementedError


def _reader():
    fake = FakeReader()
    return ControlTargetReader(readers={"ha": fake}), fake
```

Then update every existing test that mutated `ha.entities[...]` to mutate `fake.snapshots[...]` instead (state changes drive the monitor tests; e.g. `ha.entities["light.workbench"] = HomeAssistantEntity(..., "on", ...)` becomes `fake.snapshots["light.workbench"]["state"] = "on"`). Remove the `HomeAssistantEntity` import if no longer used.

Add:

```python
def test_read_unknown_kind_is_an_error():
    reader = ControlTargetReader(readers={"ha": FakeReader()})
    with pytest.raises(ValueError, match="unknown control-loop target kind: reachy"):
        reader.read("reachy:face_present")


def test_read_without_kind_is_an_error():
    reader = ControlTargetReader(readers={"ha": FakeReader()})
    with pytest.raises(ValueError, match="invalid control-loop target"):
        reader.read("light.workbench")


def test_resolve_searches_every_kind_and_prefers_exact_ids():
    class Reachy:
        def read(self, target_id):
            return {"name": "face", "state": "absent", "attributes": {}}

        def list_targets(self):
            return [TargetInfo("face_present", "Face present")]

        def verify(self, target_id, expected):
            raise NotImplementedError

    reader = ControlTargetReader(readers={"ha": FakeReader(), "reachy": Reachy()})
    keys, error = reader.resolve(["reachy:face_present", "Workbench Lamp", "sensor.temperature"])
    assert error is None
    assert keys == ["reachy:face_present", "ha:light.workbench", "ha:sensor.temperature"]


def test_resolve_with_no_readers_says_so():
    reader = ControlTargetReader(readers={})
    assert reader.resolve(["ha:light.workbench"]) == ([], "No target sources are configured.")


def test_migration_deletes_kindless_rows_and_logs_once(tmp_path, caplog):
    path = tmp_path / "loops.db"
    conn = sqlite3.connect(path)
    conn.executescript(
        """
        CREATE TABLE control_loops (
            id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL, targets TEXT NOT NULL,
            trigger_description TEXT NOT NULL, interval_seconds REAL NOT NULL,
            enabled INTEGER NOT NULL DEFAULT 1, created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
            last_checked_at TEXT, last_changed_at TEXT, last_snapshot TEXT, last_error TEXT,
            kind TEXT NOT NULL DEFAULT 'change', schedule TEXT, next_run_at TEXT
        );
        INSERT INTO control_loops (name, targets, trigger_description, interval_seconds, created_at, updated_at)
        VALUES ('kinded', '["ha:light.one"]', 'when on', 30, '2026-01-01T00:00:00+00:00', '2026-01-01T00:00:00+00:00'),
               ('kindless', '["light.two", "ha:light.three"]', 'when on', 30, '2026-01-01T00:00:00+00:00', '2026-01-01T00:00:00+00:00');
        """
    )
    conn.commit()
    conn.close()
    with caplog.at_level(logging.INFO, logger="richard.control_loops"):
        store = ControlLoopStore(path)
    assert [loop.name for loop in store.all()] == ["kinded"]
    assert "removed 1 loop(s) with kindless targets" in caplog.text
    assert sqlite3.connect(path).execute("PRAGMA user_version").fetchone()[0] == 2
    store.close()
    # Reopening is silent and idempotent.
    caplog.clear()
    ControlLoopStore(path).close()
    assert "kindless" not in caplog.text
```

Add `import logging` and `import sqlite3` at the top of the test file if missing.

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_control_loops.py -q`
Expected: FAIL with `TypeError: ControlTargetReader.__init__() got an unexpected keyword argument 'readers'`

- [ ] **Step 3: Implement**

In `src/richard/control_loops.py`:

Replace the import at line 12 with:

```python
from richard.plugins.base import TargetReader
```

Add near the constants (after `_MAX_NOTIFICATIONS`):

```python
SCHEMA_VERSION = 2  # 2: every target carries a kind prefix ("ha:light.one"); kindless rows are dropped
log = logging.getLogger(__name__)
```

(and `import logging` at the top.)

In `ControlLoopStore.__init__`, after the column-presence migration and before the final `self._conn.commit()`:

```python
            version = self._conn.execute("PRAGMA user_version").fetchone()[0]
            if version < 2:
                removed = 0
                for row_id, targets_json in self._conn.execute("SELECT id, targets FROM control_loops").fetchall():
                    targets = json.loads(targets_json)
                    if any(":" not in str(target) for target in targets):
                        self._conn.execute("DELETE FROM control_loops WHERE id = ?", (row_id,))
                        removed += 1
                if removed:
                    log.info("control loops: removed %d loop(s) with kindless targets", removed)
                self._conn.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
```

Delete `_IGNORED_ATTRIBUTES` (16-21) and `_entity_snapshot` (494-499). Replace `ControlTargetReader` (502-560) with:

```python
class ControlTargetReader:
    """Resolve and read `kind:id` targets through the readers the enabled plugins provide."""

    def __init__(self, readers: dict[str, TargetReader] | None = None) -> None:
        self._readers: dict[str, TargetReader] = dict(readers or {})

    def resolve(self, targets: object) -> tuple[list[str], str | None]:
        requested = _coerce_targets(targets)
        if not requested:
            return [], "At least one target is required."
        if not self._readers:
            return [], "No target sources are configured."
        catalog: dict[str, list[tuple[str, str]]] = {}  # kind -> [(id, name)]
        try:
            for kind, reader in self._readers.items():
                catalog[kind] = [(info.id, info.name) for info in reader.list_targets()]
        except Exception as exc:
            return [], f"Could not list targets: {exc}"
        keys: list[str] = []
        for raw in requested:
            kind, separator, identifier = raw.partition(":")
            candidates: list[str] = []
            if separator and kind in catalog:
                if any(entry_id == identifier for entry_id, _ in catalog[kind]):
                    candidates = [f"{kind}:{identifier}"]
            else:
                needle = raw.strip().lower()
                for kind_name, entries in catalog.items():
                    exact = [f"{kind_name}:{entry_id}" for entry_id, _ in entries if entry_id.lower() == needle]
                    if exact:
                        candidates.extend(exact)
                        continue
                    candidates.extend(f"{kind_name}:{entry_id}" for entry_id, name in entries if name.lower() == needle)
            if not candidates:
                return [], f"Unknown target: {raw}"
            if len(candidates) > 1:
                return [], f"Ambiguous target {raw}: " + ", ".join(candidates)
            if candidates[0] not in keys:
                keys.append(candidates[0])
        return keys, None

    def read(self, target: str) -> dict:
        kind, separator, identifier = target.partition(":")
        if not separator:
            raise ValueError(f"invalid control-loop target: {target}")
        reader = self._readers.get(kind)
        if reader is None:
            raise ValueError(f"unknown control-loop target kind: {kind}")
        return reader.read(identifier)
```

Keep whatever the existing `resolve` used to coerce its input (a list of strings, or a comma-separated string) as `_coerce_targets(targets) -> list[str]`; extract it from the current 511-550 body. Preserve the existing 32-target maximum where it is enforced today (store `create`/`update`), not in `resolve`.

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_control_loops.py tests/test_provider_control_loop.py tests/test_web.py -q`
Expected: all passed. If `tests/test_web.py` constructs `ControlTargetReader(home_assistant=...)` (it does, at lines 30-52), change it to `ControlTargetReader(readers={"ha": FakeTargetReader(entities)})` where `FakeTargetReader` wraps the existing `FakeTargetHomeAssistant` through `HomeAssistantTargetReader(FakeTargetHomeAssistant(entities))` (import from `richard.plugins.home_assistant.reader`).

- [ ] **Step 5: Full suite, then commit**

Run: `.venv/bin/python -m pytest -q`
Expected: all passed

```bash
git add src/richard/control_loops.py tests/test_control_loops.py tests/test_web.py
git commit -m "control loops: targets resolved and read by kind through plugin readers

ControlTargetReader no longer knows Home Assistant; it looks the kind prefix up
in the readers the enabled plugins provide. Schema user_version 2 drops rows with
kindless targets at open, logged once.

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 6: Diagnostics over target readers

**Files:**
- Modify: `src/richard/diagnostics.py` (whole service: 24-25, 40-77, 132-268, 271-305)
- Test: `tests/test_diagnostics.py` (FakeHomeAssistant at 6-32, `_service` helper)

**Interfaces:**
- Consumes: `TargetReader`, `TargetInfo`, `richard.verification.judge/failed`.
- Produces: `DiagnosticsService(readers: dict[str, TargetReader] | None = None)` with `refresh() -> RefreshReport`, `diagnose(target) -> Diagnosis`, `verify(target, expected) -> VerificationResult`; `SourceReport(kind: str, reachable: bool, target_count: int = 0, error: str | None = None)`; `RefreshReport(status: str, sources: tuple[SourceReport, ...])` with `summary() -> str`; `Diagnosis` unchanged; `DiagnosticsProvider(service)` unchanged tool names.

- [ ] **Step 1: Rewrite the test fake and add tests**

In `tests/test_diagnostics.py` replace `FakeHomeAssistant` and `_service` with:

```python
from richard.plugins.base import TargetInfo
from richard.plugins.home_assistant.client import HomeAssistantEntity
from richard.plugins.home_assistant.reader import HomeAssistantTargetReader


class FakeClient:
    def __init__(self, entities=None, error=None):
        self.entities = entities if entities is not None else [
            HomeAssistantEntity("light.kitchen", "on", {"friendly_name": "Kitchen Light"}),
            HomeAssistantEntity("lock.front", "locked", {"friendly_name": "Front Door"}),
        ]
        self.error = error

    def list_entities(self):
        if self.error:
            raise HomeAssistantError(self.error)
        return list(self.entities)

    def get_entity(self, entity_id):
        if self.error:
            raise HomeAssistantError(self.error)
        for entity in self.entities:
            if entity.entity_id == entity_id:
                return entity
        raise HomeAssistantError(f"{entity_id} was not found")


def _service(client=None, readers=None):
    if readers is None:
        readers = {"ha": HomeAssistantTargetReader(client or FakeClient())}
    return DiagnosticsService(readers=readers)
```

Keep every existing assertion that goes through `_service(...)`; where a test passed `home_assistant=FlakyHomeAssistant(...)`, pass `client=FlakyClient(...)` (rename the subclasses to extend `FakeClient`). Replace assertions on `report.ha_entity_count` / `report.ha_reachable` / `report.ha_error` with the new shape (see the tests below). Add:

```python
def test_refresh_reports_each_source():
    class Reachy:
        def read(self, target_id):
            return {"name": "face", "state": "absent", "attributes": {}}

        def list_targets(self):
            raise RuntimeError("daemon offline")

        def verify(self, target_id, expected):
            raise NotImplementedError

    report = _service(readers={"ha": HomeAssistantTargetReader(FakeClient()), "reachy": Reachy()}).refresh()
    assert report.status == "partial"
    assert [(s.kind, s.reachable, s.target_count, s.error) for s in report.sources] == [
        ("ha", True, 2, None), ("reachy", False, 0, "daemon offline"),
    ]
    assert report.summary() == "ha: 2 targets; reachy: unreachable (daemon offline)"


def test_refresh_with_no_sources():
    report = DiagnosticsService(readers={}).refresh()
    assert report.status == "ok"
    assert report.sources == ()
    assert report.summary() == "No target sources are configured."


def test_diagnose_by_kind_prefix_and_by_name():
    service = _service()
    by_key = service.diagnose("ha:lock.front")
    assert by_key.reachable and by_key.target == "ha:lock.front" and by_key.state["state"] == "locked"
    by_name = service.diagnose("Kitchen Light")
    assert by_name.target == "ha:light.kitchen"


def test_diagnose_unknown_kind():
    result = _service().diagnose("reachy:face_present")
    assert not result.reachable
    assert result.error == "Unknown target source: reachy"


def test_verify_delegates_to_the_reader():
    result = _service().verify("ha:light.kitchen", {"state": "on"})
    assert result.confirmed
    assert result.target == "ha:light.kitchen"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_diagnostics.py -q`
Expected: FAIL with `TypeError: DiagnosticsService.__init__() got an unexpected keyword argument 'readers'`

- [ ] **Step 3: Implement**

Rewrite the service part of `src/richard/diagnostics.py` (keep `_tool`, the three schemas and `DiagnosticsProvider`'s tool names):

```python
from richard.plugins.base import TargetReader
from richard.verification import VerificationResult, VerificationStatus


@dataclass(frozen=True)
class Match:
    kind: str
    identifier: str
    name: str

    @property
    def key(self) -> str:
        return f"{self.kind}:{self.identifier}"


@dataclass(frozen=True)
class SourceReport:
    kind: str
    reachable: bool
    target_count: int = 0
    error: str | None = None


@dataclass(frozen=True)
class RefreshReport:
    status: str  # ok | partial
    sources: tuple[SourceReport, ...] = ()

    def summary(self) -> str:
        if not self.sources:
            return "No target sources are configured."
        parts = []
        for source in self.sources:
            if source.reachable:
                parts.append(f"{source.kind}: {source.target_count} targets")
            else:
                parts.append(f"{source.kind}: unreachable ({source.error})")
        return "; ".join(parts)


@dataclass(frozen=True)
class Diagnosis:
    target: str | None = None
    name: str | None = None
    reachable: bool = False
    state: dict | None = None
    error: str | None = None
    ambiguous: tuple[str, ...] = ()

    def message(self) -> str:
        ...  # keep the existing body verbatim


class DiagnosticsService:
    """Live checks over every target source the enabled plugins provide."""

    def __init__(self, readers: dict[str, TargetReader] | None = None) -> None:
        self._readers: dict[str, TargetReader] = dict(readers or {})

    def refresh(self) -> RefreshReport:
        sources = []
        for kind, reader in self._readers.items():
            try:
                count = len(reader.list_targets())
            except Exception as exc:
                sources.append(SourceReport(kind=kind, reachable=False, error=str(exc)))
            else:
                sources.append(SourceReport(kind=kind, reachable=True, target_count=count))
        status = "ok" if all(s.reachable for s in sources) else "partial"
        return RefreshReport(status=status, sources=tuple(sources))

    def _resolve(self, target: str) -> tuple[list[Match], str | None]:
        kind, separator, identifier = target.partition(":")
        if separator and kind in self._readers:
            try:
                infos = self._readers[kind].list_targets()
            except Exception as exc:
                return [], str(exc)
            return [Match(kind, i.id, i.name) for i in infos if i.id == identifier], None
        if separator and " " not in kind:
            return [], f"Unknown target source: {kind}"
        needle = target.strip().lower()
        matches: list[Match] = []
        for kind_name, reader in self._readers.items():
            try:
                infos = reader.list_targets()
            except Exception as exc:
                return [], str(exc)
            exact = [Match(kind_name, i.id, i.name) for i in infos if i.id.lower() == needle]
            matches.extend(exact or [Match(kind_name, i.id, i.name) for i in infos if i.name.lower() == needle])
        return matches, None

    def diagnose(self, target: str) -> Diagnosis:
        matches, error = self._resolve(target)
        if error:
            return Diagnosis(target=target, error=error)
        if not matches:
            return Diagnosis(target=target, error=f"No target matches {target}.")
        if len(matches) > 1:
            return Diagnosis(target=target, ambiguous=tuple(m.key for m in matches), error="Ambiguous target.")
        match = matches[0]
        try:
            state = self._readers[match.kind].read(match.identifier)
        except Exception as exc:
            return Diagnosis(target=match.key, name=match.name, error=str(exc))
        return Diagnosis(target=match.key, name=match.name, reachable=True, state=state)

    def verify(self, target: str, expected: dict) -> VerificationResult:
        matches, error = self._resolve(target)
        if error or not matches:
            return VerificationResult(
                status=VerificationStatus.FAILED, source="diagnostics", target=target, name=target,
                action="verify", reason=error or f"No target matches {target}.",
            )
        if len(matches) > 1:
            return VerificationResult(
                status=VerificationStatus.FAILED, source="diagnostics", target=target, name=target,
                action="verify", reason="Ambiguous target: " + ", ".join(m.key for m in matches),
            )
        match = matches[0]
        return self._readers[match.kind].verify(match.identifier, expected)
```

Update `DiagnosticsProvider.context()` (280-291) so it no longer says "Home Assistant"; use: `"Diagnostics: refresh_devices lists every connected target source, diagnose_target reads one target, verify_target_state checks a target against expected values. Targets are kind:id (for example ha:light.kitchen) or a plain name."` Keep `execute` mapping as is, feeding `report.summary()`, `diagnosis.message()`, `result.message()`.

Search `tests/test_web.py` and `tests/test_cli.py` for `ha_entity_count`, `ha_reachable`, `RefreshReport(`; update any use to the new shape.

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_diagnostics.py tests/test_cli.py tests/test_web.py -q`
Expected: all passed

- [ ] **Step 5: Full suite, then commit**

```bash
git add src/richard/diagnostics.py tests/test_diagnostics.py tests/test_web.py tests/test_cli.py
git commit -m "diagnostics: refresh, diagnose and verify through plugin target readers

The service iterates readers by kind instead of holding a Home Assistant client;
RefreshReport carries one SourceReport per kind. Grading stays in verification.

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 7: Config migration, env overrides and the read-only Home Assistant view

**Files:**
- Modify: `src/richard/config.py` (`Config` field `home_assistant` at 136; `load_config` 213-225 and env block 282-306; `save_config` 359-369)
- Modify: `src/richard/cli.py` (`--set-ha-*` handlers 180-203; display 223-228)
- Modify: `src/richard/web/app.py` (`_config_to_dict` 118-130; `_apply_config_update` 368-393; `_home_assistant_status` 565-604)
- Test: `tests/test_config.py` (tests at 92, 103, 129, 141), `tests/test_cli.py` (150-176), `tests/test_web.py` (config routes)

**Interfaces:**
- Produces: `Config.home_assistant` is a **property** returning `HomeAssistant.from_table(self.plugins.tables.get("home_assistant", {}), enabled="home_assistant" in self.plugins.enabled)`; `Config.set_home_assistant(settings: HomeAssistant) -> None` writes the table and toggles the enabled list; `RICHARD_HA_*` overrides write the table; `[home_assistant]` in an existing file is migrated on load and no longer written on save.

- [ ] **Step 1: Write the failing tests**

In `tests/test_config.py`, replace the four Home Assistant tests (`test_home_assistant_defaults`, `test_home_assistant_roundtrip`, `test_home_assistant_env_overrides`, `test_home_assistant_loads_legacy_url_config`) with:

```python
def test_home_assistant_view_defaults():
    config = Config()
    assert config.home_assistant.enabled is False
    assert config.home_assistant.host == "homeassistant.local"
    assert "home_assistant" not in config.plugins.tables


def test_set_home_assistant_writes_the_plugin_table(tmp_path):
    config = Config()
    settings = HomeAssistant(enabled=True, host="ha.local", port=9443, use_https=True, token="t", timeout=5.0, verify_ssl=False)
    config.set_home_assistant(settings)
    assert config.plugins.enabled == ["home_assistant"]
    assert config.plugins.tables["home_assistant"] == {
        "host": "ha.local", "port": 9443, "use_https": True, "token": "t", "timeout": 5.0, "verify_ssl": False,
    }
    path = tmp_path / "config.toml"
    save_config(config, path)
    text = path.read_text()
    assert "[plugins.home_assistant]" in text
    assert "[home_assistant]" not in text
    again = load_config(path)
    assert again.home_assistant == settings
    settings.enabled = False
    again.set_home_assistant(settings)
    assert again.plugins.enabled == []


def test_legacy_home_assistant_table_is_migrated_and_dropped_on_save(tmp_path):
    path = tmp_path / "config.toml"
    path.write_text('[home_assistant]\nenabled = true\nurl = "https://ha-old.example:9443/api"\ntoken = "legacy"\n')
    config = load_config(path)
    assert config.plugins.enabled == ["home_assistant"]
    assert config.home_assistant.host == "ha-old.example"
    assert config.home_assistant.port == 9443
    assert config.home_assistant.use_https is True
    assert config.home_assistant.token == "legacy"
    save_config(config, path)
    assert "[home_assistant]" not in path.read_text()
    assert load_config(path).home_assistant.host == "ha-old.example"


def test_home_assistant_env_overrides_write_the_plugin_table(monkeypatch, tmp_path):
    path = tmp_path / "config.toml"
    path.write_text("")
    monkeypatch.setenv("RICHARD_HA_ENABLED", "on")
    monkeypatch.setenv("RICHARD_HA_URL", "https://ha.example:9443")
    monkeypatch.setenv("RICHARD_HA_TOKEN", "env-token")
    config = load_config(path)
    assert config.plugins.enabled == ["home_assistant"]
    assert config.home_assistant.url == "https://ha.example:9443"
    assert config.home_assistant.token == "env-token"
    monkeypatch.setenv("RICHARD_HA_ENABLED", "off")
    assert load_config(path).plugins.enabled == []
```

In `tests/test_cli.py::test_config_sets_home_assistant_fields` (150-176) keep the flags and assertions on `config.home_assistant.*` (the view serves them) and add one assertion: `assert config.plugins.enabled == ["home_assistant"]`.

In `tests/test_web.py`, find the tests that PUT Home Assistant settings through the config API (search `"home_assistant"` in request payloads) and add to one of them: after the update, `assert load_config(config_path).plugins.tables["home_assistant"]["host"] == <the host sent>`.

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_config.py tests/test_cli.py tests/test_web.py -q`
Expected: FAIL with `AttributeError: 'Config' object has no attribute 'set_home_assistant'` and the legacy/env assertions

- [ ] **Step 3: Implement**

In `src/richard/config.py`:

Remove the field `home_assistant: HomeAssistant = field(default_factory=HomeAssistant)` from `Config` and add to the class:

```python
    @property
    def home_assistant(self) -> HomeAssistant:
        """Typed view of [plugins.home_assistant]; mutate through set_home_assistant()."""
        return HomeAssistant.from_table(
            self.plugins.tables.get("home_assistant", {}),
            enabled="home_assistant" in self.plugins.enabled,
        )

    def set_home_assistant(self, settings: HomeAssistant) -> None:
        self.plugins.tables["home_assistant"] = settings.to_table()
        enabled = [name for name in self.plugins.enabled if name != "home_assistant"]
        if settings.enabled:
            enabled.append("home_assistant")
        self.plugins.enabled = enabled
```

In `load_config`, replace the `[home_assistant]` block (213-225) with the migration, placed after the `plugins` table is built (Task 3 code) and before `Config(...)`:

```python
    legacy = data.get("home_assistant")
    if isinstance(legacy, dict) and "home_assistant" not in plugins.tables:
        settings = HomeAssistant.from_table(legacy, enabled=bool(legacy.get("enabled", False)))
        plugins.tables["home_assistant"] = settings.to_table()
        if settings.enabled and "home_assistant" not in plugins.enabled:
            plugins.enabled.append("home_assistant")
```

Remove `home_assistant=home_assistant` from the `Config(...)` call. Replace the `RICHARD_HA_*` env block (282-306) with:

```python
    ha_vars = {k: v for k, v in os.environ.items() if k.startswith("RICHARD_HA_")}
    if ha_vars:
        settings = config.home_assistant
        if "RICHARD_HA_URL" in ha_vars:
            apply_home_assistant_url(settings, ha_vars["RICHARD_HA_URL"])
        if "RICHARD_HA_HOST" in ha_vars:
            settings.host = ha_vars["RICHARD_HA_HOST"]
        if "RICHARD_HA_PORT" in ha_vars:
            try:
                settings.port = int(ha_vars["RICHARD_HA_PORT"])
            except ValueError:
                pass
        if "RICHARD_HA_TOKEN" in ha_vars:
            settings.token = ha_vars["RICHARD_HA_TOKEN"]
        if "RICHARD_HA_HTTPS" in ha_vars:
            settings.use_https = ha_vars["RICHARD_HA_HTTPS"].lower() in _TRUTHY
        if "RICHARD_HA_ENABLED" in ha_vars:
            settings.enabled = ha_vars["RICHARD_HA_ENABLED"].lower() in _TRUTHY
        config.set_home_assistant(settings)
```

with `_TRUTHY = {"1", "true", "yes", "on"}` at module level (the existing block builds the same set inline; reuse it).

In `save_config`, delete the `[home_assistant]` block (359-369). Update the comment near `chmod` if it names the table.

In `src/richard/cli.py` `_run_config` (180-203): read `ha = config.home_assistant` once before the flag block, apply each flag to `ha` (same lines, `config.home_assistant.X = ...` becomes `ha.X = ...`), and after the block, if any HA flag changed: `config.set_home_assistant(ha)`.

In `src/richard/web/app.py`:
- `_config_to_dict` (118-130): unchanged (reads the view).
- `_apply_config_update` (368-393): read `ha = config.home_assistant` once, apply the eight fields to `ha`, and end with `config.set_home_assistant(ha)` when any changed.
- `_home_assistant_status` (565-604): unchanged (reads the view).

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_config.py tests/test_cli.py tests/test_web.py -q`
Expected: all passed

- [ ] **Step 5: Full suite, then commit**

```bash
git add src/richard/config.py src/richard/cli.py src/richard/web/app.py tests/test_config.py tests/test_cli.py tests/test_web.py
git commit -m "config: Home Assistant settings live in [plugins.home_assistant]

The legacy [home_assistant] table is migrated on load and no longer written;
RICHARD_HA_* overrides and the CLI/web setters go through set_home_assistant(),
and Config.home_assistant is a typed read-only view of the plugin table.

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 8: `cli.py` assembles providers through the registry

**Files:**
- Modify: `src/richard/cli.py` (`_build_home_assistant_provider` 395-415, `_build_diagnostics` 418-424, `_engine_providers` 427-435, `_build_control_loops` 438-450, `_run_chat` 252-285, `_run_voice` 495-505, `_run_serve` 579-599 and 610-616 and the `finally` at 726)
- Test: `tests/test_cli.py` (179-203, 243-308)

**Interfaces:**
- Consumes: `PluginRegistry`, `default_plugins_dir`, `DiagnosticsService(readers)`, `ControlTargetReader(readers)`.
- Produces: `_build_plugins(config, write=print) -> PluginRegistry`; `_build_diagnostics(registry) -> DiagnosticsProvider | None` (None when the registry has no target readers); `_build_control_loops(registry) -> (store, reader, provider)`; `_engine_providers(*, memory_provider, plugin_providers, control_provider, diagnostics) -> list` in the order memory, plugins, control, diagnostics; `Engine` system prompt gains the registry's context lines through a `ContextLinesProvider`.

- [ ] **Step 1: Write the failing tests**

Replace `test_home_assistant_provider_requires_enabled_complete_config` (179) and `test_home_assistant_provider_builds_when_configured` (191) and `test_build_diagnostics_uses_the_home_assistant_client_when_present` (302-308) in `tests/test_cli.py` with:

```python
def test_build_plugins_reports_a_plugin_that_cannot_build(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    config = Config()
    config.set_home_assistant(HomeAssistant(enabled=True, host="", token=None))
    lines = []
    registry = cli._build_plugins(config, lines.append)
    assert lines == ["Plugin home_assistant disabled (host or token unset; configure both before restarting Richard)"]
    assert registry.providers() == []


def test_build_plugins_wires_home_assistant_when_configured(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    config = Config()
    config.set_home_assistant(HomeAssistant(enabled=True, host="ha.local", token="t"))
    registry = cli._build_plugins(config, lambda s: None)
    assert [type(p).__name__ for p in registry.providers()] == ["HomeAssistantProvider"]
    assert list(registry.target_readers()) == ["ha"]
    assert registry.context_lines() == ["Home Assistant is connected at http://ha.local:8123."]


def test_engine_providers_order():
    providers = cli._engine_providers(
        memory_provider="memory", plugin_providers=["ha", "reachy"], control_provider="loops", diagnostics="diagnostics",
    )
    assert providers == ["memory", "ha", "reachy", "loops", "diagnostics"]
    assert cli._engine_providers(memory_provider="memory", plugin_providers=[], control_provider="loops", diagnostics=None) == ["memory", "loops"]


def test_build_diagnostics_needs_at_least_one_reader():
    from richard.plugins.registry import PluginRegistry

    empty = PluginRegistry([])
    assert cli._build_diagnostics(empty) is None
```

Keep `test_run_chat_engine_includes_diagnostics_when_ha_is_configured` (247-260) as is: it sets `RICHARD_HA_*` env, which now enables the plugin, and asserts both provider names appear.

Add:

```python
def test_serve_engine_prompt_carries_plugin_context_lines(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("RICHARD_HA_ENABLED", "on")
    monkeypatch.setenv("RICHARD_HA_HOST", "ha.local")
    monkeypatch.setenv("RICHARD_HA_TOKEN", "token")
    captured = {}
    monkeypatch.setattr(cli, "run_repl", lambda engine, convo, *a, **k: captured.update(engine=engine))
    cli.main([])
    assert "Home Assistant is connected at http://ha.local:8123." in captured["engine"]._system_prompt()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_cli.py -q`
Expected: FAIL with `AttributeError: module 'richard.cli' has no attribute '_build_plugins'`

- [ ] **Step 3: Implement**

In `src/richard/cli.py` delete `_build_home_assistant_provider` (395-415) and replace `_build_diagnostics`, `_engine_providers`, `_build_control_loops` with:

```python
class ContextLinesProvider:
    """Carries the enabled plugins' capability lines into the system prompt; no tools."""

    def __init__(self, lines: list[str]) -> None:
        self._lines = list(lines)

    def schemas(self) -> list[dict]:
        return []

    def execute(self, name: str, arguments: dict) -> str:
        return f"Unknown tool: {name}."

    def context(self) -> str | None:
        return "\n".join(self._lines) or None


def _build_plugins(config, write: Callable[[str], None] = print):
    """Discover installed plugins and build the enabled ones. Never raises for a plugin."""
    from richard.config import default_plugins_dir
    from richard.plugins.registry import PluginRegistry

    registry = PluginRegistry()
    registry.discover()
    registry.build(
        config.plugins.enabled, config.plugins.tables,
        persona_name=config.personality.name, data_dir=default_plugins_dir(), write=write,
    )
    return registry


def _build_diagnostics(registry):
    """One diagnostics service per runtime assembly; None when nothing can be read."""
    from richard.diagnostics import DiagnosticsService

    readers = registry.target_readers()
    if not readers:
        return None
    return DiagnosticsProvider(DiagnosticsService(readers=readers))


def _engine_providers(*, memory_provider, plugin_providers, control_provider, diagnostics):
    """The provider list shared by every engine: memory, plugins, loops, diagnostics."""
    providers = [memory_provider, *plugin_providers, control_provider]
    if diagnostics is not None:
        providers.append(diagnostics)
    return providers


def _build_control_loops(registry):
    """Build the shared persistent loop store, target reader, and LLM tools."""
    from richard.control_loops import ControlLoopStore, ControlTargetReader, default_control_loops_path
    from richard.providers.control_loop import ControlLoopProvider

    store = ControlLoopStore(default_control_loops_path())
    reader = ControlTargetReader(readers=registry.target_readers())
    return store, reader, ControlLoopProvider(store, reader)
```

`_run_chat` (252-285) and `_run_voice` (495-505) both become:

```python
        registry = _build_plugins(config, write)  # _run_chat: write=print
        control_store, control_reader, control_provider = _build_control_loops(registry)
        providers = _engine_providers(
            memory_provider=MemoryProvider(store),
            plugin_providers=[*registry.providers(), ContextLinesProvider(registry.context_lines())],
            control_provider=control_provider,
            diagnostics=_build_diagnostics(registry),
        )
```

`_run_serve`: replace 579-586 with `registry = _build_plugins(config, write)`, `control_store, control_reader, control_provider = _build_control_loops(registry)`, `diagnostics_provider = _build_diagnostics(registry)`, `plugin_providers = [*registry.providers(), ContextLinesProvider(registry.context_lines())]`; the `SatelliteManager(extra_providers=[...])` splat (594-598) becomes `extra_providers=[*plugin_providers, control_provider, *([diagnostics_provider] if diagnostics_provider is not None else [])]`; `_serve_providers()` (610-616) passes `plugin_providers=plugin_providers`; in the `finally` (726) add `registry.shutdown()` before `control_store.close()`.

Run `grep -n "home_assistant" src/richard/cli.py` and make sure only the `--set-ha-*` handling and the display line from Task 7 remain.

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_cli.py tests/test_satellite_manager.py -q`
Expected: all passed

- [ ] **Step 5: Full suite, then commit**

```bash
git add src/richard/cli.py tests/test_cli.py
git commit -m "cli: assemble providers from the plugin registry

One ordering everywhere (memory, plugins, loops, diagnostics), the plugins'
capability lines in the system prompt, readers for loops and diagnostics from the
registry, plugin shutdown on serve exit. Home Assistant is now only a plugin.

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 9: `richard plugins` CLI

**Files:**
- Modify: `src/richard/cli.py` (`_build_parser` 32-120, `main` 760-772), `pyproject.toml` (`slow` marker)
- Test: `tests/test_cli_plugins.py`

**Interfaces:**
- Produces: `richard plugins list`, `richard plugins enable NAME`, `richard plugins disable NAME`, `richard plugins config NAME key=value [key=value ...]`, `richard plugins install TARGET`; `_run_plugins(args, write=print) -> int`; `_parse_plugin_value(text: str) -> object` (`on/true/yes` → True, `off/false/no` → False, int, float, else str).

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_cli_plugins.py
import subprocess
import sys
import venv
from pathlib import Path

import pytest

from richard import cli
from richard.config import Config, load_config, save_config


def _run(args, tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    lines = []
    code = cli.main(["plugins", *args], write=lines.append) if "write" in cli.main.__code__.co_varnames else None
    return code, lines


def test_plugins_list_shows_installed_and_state(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("HOME", str(tmp_path))
    assert cli.main(["plugins", "list"]) == 0
    out = capsys.readouterr().out
    assert "home_assistant" in out
    assert "disabled" in out


def test_plugins_enable_and_disable_persist(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    assert cli.main(["plugins", "enable", "home_assistant"]) == 0
    assert load_config().plugins.enabled == ["home_assistant"]
    assert cli.main(["plugins", "disable", "home_assistant"]) == 0
    assert load_config().plugins.enabled == []


def test_plugins_enable_unknown_name_fails(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("HOME", str(tmp_path))
    assert cli.main(["plugins", "enable", "ghost"]) == 1
    assert "not installed" in capsys.readouterr().out


def test_plugins_config_writes_typed_values(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    assert cli.main(["plugins", "config", "home_assistant", "host=ha.local", "port=9443", "use_https=on", "timeout=2.5"]) == 0
    assert load_config().plugins.tables["home_assistant"] == {"host": "ha.local", "port": 9443, "use_https": True, "timeout": 2.5}


def test_parse_plugin_value():
    assert cli._parse_plugin_value("on") is True
    assert cli._parse_plugin_value("false") is False
    assert cli._parse_plugin_value("12") == 12
    assert cli._parse_plugin_value("1.5") == 1.5
    assert cli._parse_plugin_value("ha.local") == "ha.local"


def test_set_ha_flags_are_aliases_of_plugins_config(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    assert cli.main(["config", "--set-ha-host", "ha.local", "--set-ha-enabled", "on"]) == 0
    config = load_config()
    assert config.plugins.enabled == ["home_assistant"]
    assert config.plugins.tables["home_assistant"]["host"] == "ha.local"


@pytest.mark.slow
def test_plugins_install_editable_folder_registers_an_entry_point(tmp_path):
    folder = tmp_path / "richard-plugin-demo"
    (folder / "richard_plugin_demo").mkdir(parents=True)
    (folder / "richard_plugin_demo" / "__init__.py").write_text(
        "class DemoPlugin:\n    name = 'demo'\n    version = '0.1'\n"
        "    def config_defaults(self):\n        return {}\n"
        "    def build(self, ctx):\n        from richard.plugins.base import PluginParts\n        return PluginParts(context='Demo here.')\n"
    )
    (folder / "pyproject.toml").write_text(
        '[build-system]\nrequires = ["setuptools>=77"]\nbuild-backend = "setuptools.build_meta"\n\n'
        '[project]\nname = "richard-plugin-demo"\nversion = "0.1"\n\n'
        '[project.entry-points."richard.plugins"]\ndemo = "richard_plugin_demo:DemoPlugin"\n'
    )
    env_dir = tmp_path / "venv"
    venv.create(env_dir, with_pip=True)
    python = env_dir / "bin" / "python"
    subprocess.run([str(python), "-m", "pip", "install", "-q", "-e", str(folder)], check=True)
    probe = "from importlib import metadata; print([e.name for e in metadata.entry_points(group='richard.plugins')])"
    out = subprocess.run([str(python), "-c", probe], check=True, capture_output=True, text=True).stdout
    assert "demo" in out
    assert cli._install_command(folder, python=str(python)) == [str(python), "-m", "pip", "install", "-e", str(folder)]
```

Remove the unused `_run` helper if `cli.main` has no `write` parameter (check `main` at 760-772; it does not, so drop `_run`).

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_cli_plugins.py -q`
Expected: FAIL with `SystemExit: 2` (argparse: invalid choice 'plugins') and `AttributeError: module 'richard.cli' has no attribute '_parse_plugin_value'`

- [ ] **Step 3: Implement**

In `pyproject.toml`:

```toml
markers = [
    "gpu: needs CUDA and real model downloads; excluded from the default run",
    "slow: creates virtualenvs or installs packages; excluded from the default run",
]
addopts = "-m 'not gpu and not slow'"
```

In `src/richard/cli.py`, in `_build_parser` add the subcommand:

```python
    plugins_parser = subparsers.add_parser("plugins", help="List, enable, disable, configure or install plugins")
    plugins_sub = plugins_parser.add_subparsers(dest="plugins_command", required=True)
    plugins_sub.add_parser("list", help="Installed plugins and their state")
    plugins_sub.add_parser("enable", help="Enable a plugin").add_argument("name")
    plugins_sub.add_parser("disable", help="Disable a plugin").add_argument("name")
    config_p = plugins_sub.add_parser("config", help="Set plugin settings: key=value ...")
    config_p.add_argument("name")
    config_p.add_argument("pairs", nargs="+", metavar="key=value")
    plugins_sub.add_parser("install", help="pip install a plugin (folder = editable, else pypi name or git url)").add_argument("target")
```

Add the handlers:

```python
def _parse_plugin_value(text: str) -> object:
    lowered = text.strip().lower()
    if lowered in ("on", "true", "yes"):
        return True
    if lowered in ("off", "false", "no"):
        return False
    try:
        return int(text)
    except ValueError:
        pass
    try:
        return float(text)
    except ValueError:
        return text


def _install_command(target: str | Path, *, python: str = sys.executable) -> list[str]:
    target_path = Path(str(target)).expanduser()
    if target_path.is_dir():
        return [python, "-m", "pip", "install", "-e", str(target_path)]
    return [python, "-m", "pip", "install", str(target)]


def _run_plugins(args, write: Callable[[str], None] = print) -> int:
    from richard.plugins.registry import PluginRegistry

    config = load_config()
    registry = PluginRegistry()
    registry.discover()
    installed = {record.name: record for record in registry.records()}
    if args.plugins_command == "list":
        registry.build(
            config.plugins.enabled, config.plugins.tables,
            persona_name=config.personality.name, data_dir=default_plugins_dir(), write=lambda s: None,
        )
        for record in registry.records():
            detail = f" ({record.error})" if record.error else ""
            write(f"{record.name:<20} {record.version:<10} {record.status}{detail}  [{record.module}]")
        if not registry.records():
            write("No plugins installed.")
        return 0
    if args.plugins_command in ("enable", "disable"):
        if args.plugins_command == "enable" and args.name not in installed:
            write(f"Plugin {args.name} is not installed (richard plugins list).")
            return 1
        enabled = [name for name in config.plugins.enabled if name != args.name]
        if args.plugins_command == "enable":
            enabled.append(args.name)
        config.plugins.enabled = enabled
        save_config(config)
        write(f"{args.name} {args.plugins_command}d. Restart Richard to apply.")
        return 0
    if args.plugins_command == "config":
        table = config.plugins.table(args.name)
        for pair in args.pairs:
            key, separator, value = pair.partition("=")
            if not separator or not key:
                write(f"Expected key=value, got {pair!r}.")
                return 1
            table[key.strip()] = _parse_plugin_value(value)
        save_config(config)
        write(f"{args.name}: " + ", ".join(f"{k}={v}" for k, v in table.items() if k != "token"))
        return 0
    if args.plugins_command == "install":
        command = _install_command(args.target)
        write("$ " + " ".join(command))
        completed = subprocess.run(command)
        if completed.returncode != 0:
            return completed.returncode
        write("Installed. Enable it with: richard plugins enable <name>")
        return 0
    return 2
```

Imports needed at the top of `cli.py`: `import subprocess`, `import sys`, `from pathlib import Path`, `from richard.config import default_plugins_dir` (extend the existing `from richard.config import ...` line). In `main` dispatch: `if args.command == "plugins": return _run_plugins(args)`.

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_cli_plugins.py -q` (the slow test is excluded by default) then once `.venv/bin/python -m pytest tests/test_cli_plugins.py -q -m slow`
Expected: 6 passed; then 1 passed (about 20 s)

- [ ] **Step 5: Full suite, then commit**

```bash
git add src/richard/cli.py pyproject.toml tests/test_cli_plugins.py
git commit -m "cli: richard plugins list/enable/disable/config/install

Editable pip install for a folder keeps a plugin a visible directory while its
dependencies are real; --set-ha-* remain as aliases writing the plugin table.

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 10: Event-source slot in the control-loop monitor

**Files:**
- Modify: `src/richard/control_loops.py` (`ControlLoopMonitor` 607-783), `src/richard/cli.py` (`_start_control_loop_monitor` 453-467 and its call at 647-657)
- Test: `tests/test_control_loops.py`

**Interfaces:**
- Consumes: `Event`, `EventSource` from Task 1; `registry.event_sources()`.
- Produces: `ControlLoopMonitor(..., event_sources: Iterable[EventSource] = ())`; `push(event: Event) -> None` (thread-safe); `check_once()` first drains pushed events and force-checks every enabled loop watching `event.target`, returning their changes; `start()` subscribes every source with `self.push`; `stop()` calls each source's stop function.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_control_loops.py`:

```python
from richard.plugins.base import Event


class FakeSource:
    def __init__(self):
        self.sink = None
        self.stopped = False

    def __call__(self, sink):
        self.sink = sink
        return lambda: setattr(self, "stopped", True)


def _monitor_with_source(tmp_path, clock):
    store = ControlLoopStore(tmp_path / "loops.db")
    reader = _StubReader(snapshots={"fake:x": {"name": "X", "state": "absent", "attributes": {}}})
    loop = store.create(name="face", targets=["fake:x"], trigger_description="when present", interval_seconds=3600)
    source = FakeSource()
    notified = []
    monitor = ControlLoopMonitor(store, reader, lambda change: notified.append(change) or "seen", monotonic=clock, event_sources=[source])
    return store, reader, loop, source, notified, monitor


def test_pushed_event_force_checks_the_watching_loop(tmp_path):
    now = [1000.0]
    store, reader, loop, source, notified, monitor = _monitor_with_source(tmp_path, lambda: now[0])
    monitor.check_once()  # baseline snapshot, nothing to notify
    assert notified == []
    reader.snapshots["fake:x"]["state"] = "present"
    assert monitor.check_once() == []  # interval (1 h) not elapsed: polling would wait
    monitor.push(Event(kind="fake", target="fake:x", payload={"state": "present"}))
    changes = monitor.check_once()
    assert len(changes) == 1 and changes[0].loop_id == loop.id
    assert notified and "present" in notified[0].llm_prompt()
    store.close()


def test_start_subscribes_sources_and_stop_unsubscribes(tmp_path):
    store, reader, loop, source, notified, monitor = _monitor_with_source(tmp_path, time.monotonic)
    monitor.start()
    try:
        assert source.sink == monitor.push
    finally:
        assert monitor.stop()
    assert source.stopped
    store.close()


def test_event_for_an_unwatched_target_is_ignored(tmp_path):
    store, reader, loop, source, notified, monitor = _monitor_with_source(tmp_path, lambda: 5.0)
    monitor.check_once()
    monitor.push(Event(kind="fake", target="fake:other"))
    assert monitor.check_once() == []
    store.close()
```

`_StubReader` already exists at lines 329-340; `time` may need importing. Check that `ControlLoopChange` has `loop_id` (it does, per the dataclass at 64-83; if the field is named differently use that name).

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_control_loops.py -q -k "pushed or subscribes or unwatched"`
Expected: FAIL with `TypeError: ControlLoopMonitor.__init__() got an unexpected keyword argument 'event_sources'`

- [ ] **Step 3: Implement**

In `ControlLoopMonitor.__init__` add the parameter `event_sources: Iterable[EventSource] = ()` (import `Iterable` from `collections.abc`, `Event, EventSource` from `richard.plugins.base`) and the state:

```python
        self._event_sources = list(event_sources)
        self._source_stops: list[Callable[[], None]] = []
        self._events: collections.deque[Event] = collections.deque()
        self._events_lock = threading.Lock()
```

Add `self._wake = threading.Event()` to `__init__`, then:

```python
    def push(self, event: Event) -> None:
        """Entry point for plugin event sources; safe from any thread."""
        with self._events_lock:
            self._events.append(event)
        self._wake.set()  # the polling thread checks on the next wake, not the next second
```

In `_run`, replace `self._stop.wait(self._poll_seconds)` with `self._wake.wait(self._poll_seconds); self._wake.clear()` and keep `while not self._stop.is_set():` as the loop condition; `stop()` must also call `self._wake.set()` so a stop is not delayed by a full poll interval.

In `start()`, after the thread starts:

```python
        for source in self._event_sources:
            try:
                self._source_stops.append(source(self.push))
            except Exception:
                log.error("event source failed to start:\n%s", traceback.format_exc())
```

In `stop()`, before joining:

```python
        for stop_source in self._source_stops:
            try:
                stop_source()
            except Exception:
                log.error("event source failed to stop:\n%s", traceback.format_exc())
        self._source_stops.clear()
```

At the top of `check_once`:

```python
        results: list[ControlLoopChange | ControlLoopScheduledCheck] = []
        with self._events_lock:
            pushed = list(self._events)
            self._events.clear()
        if pushed:
            targets = {event.target for event in pushed}
            for loop in self._store.all(enabled_only=True):
                if loop.kind == "change" and targets.intersection(loop.targets):
                    change = self._check_loop(loop)
                    if change is not None:
                        results.append(change)
```

and let the existing body append into the same `results` list (rename its local accordingly) so force-checked loops are not checked twice in one tick (skip a loop in the polling pass if its id is already in `{c.loop_id for c in results}`).

In `cli._start_control_loop_monitor` add a keyword `event_sources=()` passed through to `ControlLoopMonitor(...)`, and at the call site in `_run_serve` pass `event_sources=registry.event_sources()`.

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_control_loops.py tests/test_cli.py -q`
Expected: all passed

- [ ] **Step 5: Full suite, then commit**

```bash
git add src/richard/control_loops.py src/richard/cli.py tests/test_control_loops.py
git commit -m "control loops: event-source slot, a pushed event is a polled change

Plugins may push Events; the monitor force-checks every enabled loop watching
that target on the next tick and wakes immediately. No real source yet; the
Reachy body plugin (spec four) is the first.

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 11: Docs

**Files:**
- Create: `docs/plugins.md`
- Modify: `README.md` (one paragraph in the features/architecture section)

- [ ] **Step 1: Write `docs/plugins.md`**

```markdown
# Plugins

Richard's connections are plugins: installable packages discovered through the
`richard.plugins` entry-point group. A plugin contributes tools (the `Provider`
protocol), target readers for control loops and diagnostics (keyed by the kind used
in targets, `ha:light.kitchen`), event sources that push changes instead of being
polled, and one capability line for the system prompt. A disabled plugin is invisible
to the model, so "what can you do" is answered from what is enabled.

## Using plugins

    richard plugins list                      # installed, enabled, version, build status
    richard plugins enable home_assistant
    richard plugins config home_assistant host=ha.local port=8123 token=...
    richard plugins disable home_assistant
    richard plugins install ./my-plugin       # folder: editable install; else a PyPI name or git URL

Settings live in `~/.richard/config.toml`:

    [plugins]
    enabled = ["home_assistant"]

    [plugins.home_assistant]
    host = "ha.local"
    port = 8123
    token = "..."

Richard preserves every `[plugins.*]` table it does not model. Restart Richard after
enabling or configuring a plugin. A plugin that fails to build is logged with its
traceback and skipped; Richard still starts.

## Writing one

```python
# my_plugin/__init__.py
from richard.plugins.base import PluginContext, PluginParts, TargetInfo

class MyReader:
    def read(self, target_id: str) -> dict:
        return {"name": target_id, "state": "on", "attributes": {}}
    def list_targets(self) -> list[TargetInfo]:
        return [TargetInfo("thing", "The thing")]
    def verify(self, target_id: str, expected: dict):
        ...  # return a richard.verification.VerificationResult

class MyPlugin:
    name = "my"          # entry-point name and config table name
    version = "0.1"
    def config_defaults(self) -> dict:
        return {"host": "localhost"}
    def build(self, ctx: PluginContext) -> PluginParts:
        host = ctx.config["host"]          # [plugins.my] with defaults applied
        return PluginParts(
            providers=[],                   # Provider objects: schemas(), execute(), context()
            target_readers={"my": MyReader()},
            event_sources=[],               # callables: start(sink) -> stop()
            context=f"My device is reachable at {host}.",
        )
```

```toml
# pyproject.toml
[project.entry-points."richard.plugins"]
my = "my_plugin:MyPlugin"
```

`ctx.data_dir` (`~/.richard/plugins/<name>/`) is yours for state. Raise in `build`
when the configuration is unusable; the registry reports it and moves on.

Event sources: `def start(sink): ...; return stop` — call `sink(Event(kind, target,
payload))` from any thread; the control-loop monitor force-checks every loop watching
that target on its next tick.
```

- [ ] **Step 2: README paragraph**

Add to `README.md`, after the features list:

> **Plugins.** Connections (Home Assistant today, the Reachy body next) are plugins discovered through Python entry points: `richard plugins list|enable|disable|config|install`. A disabled plugin contributes no tools and no prompt text. See `docs/plugins.md`.

- [ ] **Step 3: Commit**

```bash
git add docs/plugins.md README.md
git commit -m "docs: plugins (using and writing one)

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

## Self-review against the spec

- **Decision 1, entry points + `richard plugins install`**: Tasks 2 (discovery), 4 (built-in registration in pyproject), 9 (CLI incl. editable install, slow test). ✓
- **Decision 2, private device package separate**: nothing added here; the public/private grep is in the global constraints. ✓
- **Decision 3, `plugin:id` mandatory, schema bump, kindless rows deleted with one log line**: Task 5 (`SCHEMA_VERSION = 2`, `PRAGMA user_version`, log line, idempotent reopen test). ✓
- **Decision 4, event-source slot in core, sources in plugins**: Task 1 (`Event`, `EventSource`), Task 2 (`event_sources()`), Task 10 (monitor drain, fake source test). ✓
- **Decision 5, voice engines stay config**: untouched. ✓
- **Contract** (`PluginContext`, `PluginParts`, `Plugin`, `TargetReader`, `EventSource`): Task 1. The spec's comment "entry-point name == kind used in loop targets" is refined: the kind is the key the plugin registers in `target_readers` (`ha`), the name is the entry point and config table (`home_assistant`), so existing `ha:` targets survive the migration. ✓
- **Registry and lifecycle** (discover, build only enabled in list order with try/except, accessors, shutdown): Task 2; runtime enable/disable via CLI + restart, web hooks out of scope. ✓
- **Configuration** (`[plugins]` tables round-trip, migration from `[home_assistant]`, env overrides mapped, CLI with `--set-ha-*` aliases): Tasks 3, 7, 9. ✓
- **Where the line falls / diagnostics through readers**: Tasks 4, 5, 6, 8. ✓
- **Capability context** (`context_lines()` appended after the persona): Task 8 (`ContextLinesProvider`). ✓
- **Testing list**: registry with fakes (Task 2), HA behind the plugin path with existing tests kept (Tasks 4-8), config round-trip + migration + env (Tasks 3, 7), loops `plugin:id` + unknown kind + migration (Task 5), CLI (Task 9), entry-point discovery through a real `EntryPoint` (Task 2). ✓
- **Placeholders**: the only `...` bodies are in `docs/plugins.md` example prose and in `Diagnosis.message()` where the existing body is kept verbatim (Task 6 says so).
- **Type consistency**: `TargetReader.read` returns the control-loop snapshot shape everywhere (`{"name","state","attributes"}`); `verify` returns `VerificationResult` from the reader (Task 4) and is what `DiagnosticsService.verify` returns (Task 6); `PluginRegistry.target_readers()` feeds both `ControlTargetReader(readers=...)` (Task 5) and `DiagnosticsService(readers=...)` (Task 6) from `cli._build_plugins` (Task 8); `Config.set_home_assistant`/`home_assistant` (Task 7) are what `cli._run_config` and `web._apply_config_update` use.
