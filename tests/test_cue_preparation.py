"""Exercise cache reuse and asynchronous preparation with only TTS replaced."""
import json
import threading
import time
from copy import deepcopy

from richard.config import Config, save_config
from richard.memory import MemoryStore
from richard.realtime import cues
from richard.web.app import WebApp


def config(language="auto"):
    result = Config()
    result.voice.language = language
    result.voice.tts_engine = "remote"
    result.voice.tts_voice = "test-voice"
    return result


class Synth:
    samplerate = 24000

    def synth(self, text):
        return b"\x01\x00" * 240


def eventually(predicate):
    deadline = time.monotonic() + 3
    while time.monotonic() < deadline:
        value = predicate()
        if value:
            return value
        time.sleep(0.005)
    raise AssertionError("background preparation did not settle")


def test_switching_language_and_voice_reuses_each_prepared_bank(tmp_path):
    original = config("en")
    first = cues.prepare_cues(original, tmp_path, synth=Synth())
    italian = deepcopy(original)
    italian.voice.language = "it"
    cues.prepare_cues(italian, tmp_path, synth=Synth())
    other = deepcopy(original)
    other.voice.tts_voice = "other-voice"
    cues.prepare_cues(other, tmp_path, synth=Synth())
    assert cues.read_cues(original, tmp_path) == first
    assert len(cues.read_cues(italian, tmp_path)["clips"]) == 6


def test_preparation_uses_the_catalog_language_without_mutating_settings(tmp_path, monkeypatch):
    cfg = config("it")
    cfg.voice.tts_language = "English"
    built = []
    def build(snapshot, write):
        built.append(snapshot.voice.tts_language)
        return Synth()
    monkeypatch.setattr("richard.cli._build_tts", build)
    cues.prepare_cues(cfg, tmp_path)
    assert built == ["Italian"]
    assert cfg.voice.tts_language == "English"


def test_manual_prepare_returns_while_tts_blocked_and_coalesces_duplicates(tmp_path, monkeypatch):
    entered, release = threading.Event(), threading.Event()
    calls = []
    class BlockingSynth(Synth):
        def synth(self, text):
            calls.append(text)
            entered.set()
            assert release.wait(3)
            return super().synth(text)
    monkeypatch.setattr("richard.cli._build_tts", lambda *args: BlockingSynth())
    cfg = config()
    path = tmp_path / "config.toml"
    save_config(cfg, path)
    app = WebApp(config_path=path, memory_store=MemoryStore(":memory:"))
    try:
        response = app.handle("POST", "/api/realtime/cues/prepare", b'{"force":true}')
        assert response.status == 202
        assert entered.wait(1)
        duplicate = app.handle("POST", "/api/realtime/cues/prepare", b'{"force":true}')
        assert duplicate.status == 202
        pending = json.loads(app.handle("GET", "/api/realtime/cues").body)
        assert pending["preparation"]["state"] == "preparing"
        assert pending["preparation"]["total"] == 12
        release.set()
        def finished():
            data = json.loads(app.handle("GET", "/api/realtime/cues").body)
            return data if data.get("preparation", {}).get("state") == "ready" else None
        bank = eventually(finished)
        assert set(bank["banks"]) == {"en", "it"}
        assert all(len(b["clips"]) == 6 for b in bank["banks"].values())
        assert len(calls) == 12
    finally:
        release.set()
        if hasattr(app, "close"):
            app.close()


def test_saving_voice_settings_prepares_missing_bank_and_failure_can_retry(tmp_path, monkeypatch):
    failing = True
    def build(*args):
        if failing:
            raise RuntimeError("private endpoint failure detail")
        return Synth()
    monkeypatch.setattr("richard.cli._build_tts", build)
    path = tmp_path / "config.toml"
    save_config(config("en"), path)
    app = WebApp(config_path=path, memory_store=MemoryStore(":memory:"))
    try:
        response = app.handle("PUT", "/api/config", b'{"voice":{"language":"it"}}')
        assert response.status == 200
        def status():
            return json.loads(app.handle("GET", "/api/realtime/cues").body)
        data = eventually(lambda: (d if (d := status()).get("preparation", {}).get("state") == "error" else None))
        assert "private endpoint" not in json.dumps(data)
        failing = False
        assert app.handle("POST", "/api/realtime/cues/prepare", b"{}").status == 202
        data = eventually(lambda: (d if (d := status()).get("preparation", {}).get("state") == "ready" else None))
        assert len(data["banks"]["it"]["clips"]) == 6
    finally:
        if hasattr(app, "close"):
            app.close()


def test_prepare_rejects_non_boolean_force(tmp_path):
    path = tmp_path / "config.toml"
    save_config(config(), path)
    app = WebApp(config_path=path, memory_store=MemoryStore(":memory:"))
    assert app.handle("POST", "/api/realtime/cues/prepare", b'{"force":"false"}').status == 400


def test_saved_variants_have_a_disk_bound_and_keep_the_current_bank(tmp_path):
    for index in range(12):
        cfg = config("en")
        cfg.voice.tts_voice = f"voice-{index}"
        cues.prepare_cues(cfg, tmp_path, synth=Synth())
    assert len(list(tmp_path.glob("*.json"))) <= 8
    assert len(cues.read_cues(cfg, tmp_path)["clips"]) == 6


def test_preparation_waits_for_voice_to_close_then_uses_latest_saved_settings(tmp_path, monkeypatch):
    from richard.realtime.cue_preparation import CuePreparation
    allowed = threading.Event()
    built = []
    def build(snapshot, write):
        built.append(snapshot.voice.tts_voice)
        return Synth()
    monkeypatch.setattr("richard.cli._build_tts", build)
    first, latest = config("en"), config("it")
    latest.voice.tts_voice = "latest"
    manager = CuePreparation(tmp_path, can_prepare=allowed.is_set)
    try:
        manager.request(first)
        assert manager.status(first)["state"] == "queued"
        manager.request(latest)
        assert not built
        allowed.set()
        eventually(lambda: manager.status(latest)["state"] == "ready")
        assert built == ["latest"]
        assert not cues.read_cues(first, tmp_path)["clips"]
    finally:
        manager.close()
