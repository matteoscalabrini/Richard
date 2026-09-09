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

The web UI's Plugins page lists the same plugins with their running state and toggles the
enabled list; restart Richard to apply, as with the CLI.

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
