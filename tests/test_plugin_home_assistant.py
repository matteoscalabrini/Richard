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
