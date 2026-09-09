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
