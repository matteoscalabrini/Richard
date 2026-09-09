from pathlib import Path

import pytest

pytest.importorskip("PIL")

from richard.perception.reader import PerceptionTargetReader  # noqa: E402
from richard.plugins.base import PluginContext, TargetInfo  # noqa: E402
from richard.plugins.perception import PerceptionPlugin  # noqa: E402
from richard.plugins.registry import PluginRegistry  # noqa: E402
from richard.verification import VerificationStatus  # noqa: E402


class FakeService:
    def __init__(self, present=(), gallery=("matteo",)):
        self._present = list(present)
        self._gallery = [{"name": n, "samples": 1, "enrolled_at": "x"} for n in gallery]

        class G:
            def list(inner):
                return self._gallery
        self.gallery = G()

    def presence(self):
        return [{"source": "browser", "subject": s, "since": 1.0} for s in self._present]


def test_reader_lists_and_reads_presence_targets():
    reader = PerceptionTargetReader(FakeService(present=["matteo"]))
    assert reader.list_targets() == [TargetInfo(id="person_present", name="Someone present"),
                                     TargetInfo(id="identified:matteo", name="matteo present")]
    assert reader.read("person_present") == {"name": "Someone present", "state": "on", "attributes": {"present": ["matteo"]}}
    assert reader.read("identified:matteo")["state"] == "on"
    assert reader.read("identified:guest")["state"] == "off"
    with pytest.raises(KeyError):
        reader.read("weather")


def test_reader_verify_confirms_and_mismatches():
    reader = PerceptionTargetReader(FakeService(present=["matteo"]))
    assert reader.verify("person_present", {"state": "on"}).status == VerificationStatus.CONFIRMED
    assert reader.verify("identified:guest", {"state": "on"}).status == VerificationStatus.MISMATCH
    assert reader.verify("nope", {"state": "on"}).status == VerificationStatus.FAILED


def test_plugin_builds_parts_with_injected_detectors(tmp_path):
    class Det:
        def detect(self, rgb):
            return []

    plugin = PerceptionPlugin(detector_factory=lambda settings, write: Det(), identifier_factory=lambda settings, gallery, write: None)
    ctx = PluginContext(config={**plugin.config_defaults(), "sensitivity": 70}, persona_name="Richard",
                        data_dir=tmp_path / "perception", write=lambda s: None)
    parts = plugin.build(ctx)
    names = [s["function"]["name"] for p in parts.providers for s in p.schemas()]
    assert names == ["who_is_here", "last_seen"]  # camera withheld: no live source yet
    assert "perception" in parts.target_readers and len(parts.event_sources) == 1
    assert "perception" in parts.context.lower()
    assert plugin.service.settings.sensitivity == 70 and plugin.service.started is True
    assert Path(tmp_path / "perception" / "perception.db").exists()
    parts.shutdown()
    assert plugin.service.started is False


def test_plugin_defaults_have_the_documented_keys():
    keys = set(PerceptionPlugin().config_defaults())
    assert keys == {"identity_enabled", "quiet_hours", "sensitivity", "cooldown_s", "enter_debounce_s",
                    "leave_debounce_s", "stream_fps", "keep_thumbnails", "device", "stale_s"}


def test_registry_exposes_a_built_plugin_instance(tmp_path):
    plugin = PerceptionPlugin(detector_factory=lambda s, w: None, identifier_factory=lambda s, g, w: None)
    registry = PluginRegistry([plugin])
    assert registry.plugin("perception") is plugin
    assert registry.plugin("nope") is None
    registry.build(["perception"], {}, persona_name="R", data_dir=tmp_path, write=lambda s: None)
    registry.shutdown()
