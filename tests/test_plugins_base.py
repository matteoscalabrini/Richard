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
