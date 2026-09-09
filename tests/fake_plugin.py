"""A plugin that a real importlib.metadata.EntryPoint can load in tests."""
from richard.plugins.base import PluginParts


class FakePlugin:
    name = "fake"
    version = "9.9"

    def config_defaults(self):
        return {"greeting": "hi", "retries": 1}

    def build(self, ctx):
        return PluginParts(context=f"Fake ({ctx.config['greeting']}, retries {ctx.config['retries']})")
