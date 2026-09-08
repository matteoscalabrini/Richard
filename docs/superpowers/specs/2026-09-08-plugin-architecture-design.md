# Plugin architecture for Richard's connections — design (spec zero)

Status: approved in discussion 2026-09-07 (Matteo: entry points, private device package, mandatory
target kinds with no compatibility shim, event sources in plugins, voice engines stay config).
Precedes spec one (realtime GA + inline action markers) because spec one adds providers and they
should be born as plugins.

## Why

Richard's connections (Home Assistant today, a private device integration, the Reachy body next) are welded into
the core: `control_loops.py` imports the Home Assistant client, `DiagnosticsService` is built with
it, and `cli.py` hand-assembles the provider list in three places with "if home_assistant is not
None" branches. Matteo's requirement (2026-09-07): treat connections as installable plugins so
Richard's functions can be limited or expanded as needed. Two consequences make it worth doing now:

- **Capability scoping becomes structural.** `PHILOSOPHY_REACHY.md` grants exploration freedom
  without permission for household actions. With plugins, that is "the household plugin is not
  enabled", not a prompt asking the model to behave. Disabled plugins are invisible to the model.
- **The public/private split becomes packaging.** the private device integration lives in a package that the public
  tree never imports, so the publishing rule stops being branch hygiene.

## Decisions

1. **Discovery by Python entry points**, group `richard.plugins`. Built-ins register through
   Richard's own package metadata. `richard plugins install <folder|git-url|pypi-name>` runs an
   editable pip install for a folder, so a plugin stays a visible, editable directory while
   dependencies and versions are real. No directory scanner, no static registry.
2. **The private device integration is a separate private package** on the private remote. Not in this repo.
3. **Loop targets are namespaced `plugin:id`** (`ha:light.kitchen`, `reachy:face_present`).
   Mandatory. The control-loop schema bumps; rows whose targets lack a kind are deleted at
   migration with one log line. No default kind, no compatibility shim.
4. **Event sources are a slot in the core contract; the sources live in plugins.** The loop monitor
   subscribes to whatever enabled plugins push. Home Assistant never uses it; the Reachy plugin
   (spec four) is the first to fill it. Added now so spec four is not a retrofit.
5. **Voice engines stay config choices**, not plugins.

## The contract

```python
# richard/plugins/base.py
@dataclass(frozen=True)
class PluginContext:
    config: dict            # the plugin's [plugins.<name>] table, defaults applied
    persona_name: str
    data_dir: Path          # ~/.richard/plugins/<name>/ for plugin-owned state
    write: Callable[[str], None]

@dataclass
class PluginParts:
    providers: list[Provider] = field(default_factory=list)          # existing Provider protocol
    target_readers: dict[str, TargetReader] = field(default_factory=dict)  # kind -> reader
    event_sources: list[EventSource] = field(default_factory=list)
    context: str | None = None                                        # one capability line
    shutdown: Callable[[], None] | None = None

class Plugin(Protocol):
    name: str               # entry-point name == kind used in loop targets
    version: str
    def config_defaults(self) -> dict: ...
    def build(self, ctx: PluginContext) -> PluginParts: ...
```

- `Provider` is unchanged: `schemas()`, `execute(name, arguments)`, `context()`.
- `TargetReader` is the interface `ControlTargetReader` and `DiagnosticsService` need from a
  connection: `read(target_id) -> Snapshot`, `list_targets() -> list[TargetInfo]`,
  `verify(target_id, expected) -> VerificationResult`. The grading in `verification.py` stays core;
  the reads move behind the reader.
- `EventSource` is a callable that accepts a sink and returns a stop function:
  `start(sink: Callable[[Event], None]) -> Callable[[], None]`. `Event` carries `kind`, `target`,
  `payload`, `observed_at`. The monitor treats a pushed event like a polled change.

## Registry and lifecycle

- `PluginRegistry.discover()` reads entry points, imports lazily, records `name`, `version`, module.
- `PluginRegistry.build(config)` builds only `config.plugins.enabled`, in list order, each in a
  try/except: a plugin that raises in `build` is logged with its traceback and skipped. Richard
  never fails to start because of a plugin (same rule as the realtime server today).
- Provider lists are already rebuilt per session in the serve path; the registry exposes
  `providers()`, `target_readers()`, `event_sources()`, `context_lines()` so `_engine_providers`,
  the loop monitor and diagnostics ask the registry instead of `cli.py` assembling lists.
- Enable/disable at runtime rebuilds the affected plugin: providers take effect on the next engine
  build, readers and sources on the next monitor tick. Web UI hooks are a later addition.

## Configuration

```toml
[plugins]
enabled = ["home_assistant"]

[plugins.home_assistant]
host = "..."
port = 8123
token = "..."
```

- `save_config` currently rebuilds the file from the dataclass and would drop unknown tables. It
  learns to preserve `[plugins.*]` tables it does not model (round-trip test required).
- One-time migration: an existing `[home_assistant]` table with `enabled = true` becomes
  `[plugins.home_assistant]` plus `enabled = ["home_assistant"]`; the old table is removed after
  a successful save. Environment overrides (`RICHARD_HA_*`) keep working by mapping onto the
  plugin table.
- CLI: `richard plugins list` (discovered, enabled, version, build status), `enable NAME`,
  `disable NAME`, `install TARGET`, `config NAME key=value`. The old `--set-ha-*` flags become
  thin aliases for `plugins config home_assistant ...`.

## Where the line falls

| core | plugins |
|---|---|
| engine, brains and roles, persona | Home Assistant (first, in-repo) |
| memory, conversation | private device integration (private package) |
| control-loop engine and scheduler | Reachy body: REST side + presence events (spec four) |
| verification grading | later utilities (weather, time, search) |
| realtime, satellite, web hosts | |
| voice engines | |

Diagnostics keeps its grading and report shapes in core; `refresh`/`diagnose`/`verify` iterate the
registry's target readers instead of one Home Assistant client.

## Capability context

`PluginRegistry.context_lines()` yields one sentence per enabled plugin ("Home Assistant:
42 entities reachable at ..."), appended to the system prompt after the persona. A plugin that is
disabled contributes no schemas and no line, so "what can you do" is answered from what is enabled.

## Testing

- Unit: registry with an in-memory list of fake plugins (no entry points needed), enable/disable,
  build failure isolation, context lines, target-reader lookup by kind, event-source subscription.
- Home Assistant behind the plugin path: every existing Home Assistant test keeps passing with the
  provider, reader and diagnostics obtained from the registry.
- Config: `[plugins.*]` round-trip, migration from `[home_assistant]`, env overrides.
- Loops: `plugin:id` parsing, unknown kind is a loop health error not a crash, migration deletes
  kindless rows and logs the count.
- CLI: `plugins list/enable/disable/config` against a temp config; `install` is exercised with a
  local folder in a temp venv only in an integration test marked slow.
- Entry-point discovery itself is covered by one test that registers a fake plugin through
  `importlib.metadata` shims.

## Migration steps (one plan, in order)

1. Registry, contracts, config tables and round-trip, CLI.
2. Home Assistant moved into `richard.plugins.home_assistant` with its own config defaults;
   `cli.py` assembly replaced by registry calls; diagnostics and loops read through target readers.
3. Loop target kinds and the schema bump.
4. Event-source slot wired into the monitor with a fake source test. No real source yet.
5. Docs: `docs/plugins.md` (how to write and install one), README paragraph.

## Non-goals

- No plugin sandboxing; a plugin is trusted code, same as today's providers.
- No web UI for plugins in this spec.
- No Reachy plugin implementation here; only the contract it will use.
- Voice engines, brains and memory are not plugins.
