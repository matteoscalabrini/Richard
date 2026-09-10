from __future__ import annotations

import base64
import json
from pathlib import Path

import pytest

from richard.config import Config, save_config
from richard.control_loops import ControlLoopStore
from richard.memory import MemoryStore
from richard.realtime.cues import cue_fingerprint, main, prepare_cues, read_cues
from richard.satellite.relays import RelayRegistry
from richard.web import WebApp


class FakeSynth:
    samplerate = 24000

    def __init__(self, *, marker: int = 1, fail_at: int | None = None) -> None:
        self.marker = marker
        self.fail_at = fail_at
        self.calls: list[str] = []

    def synth(self, text: str) -> bytes:
        self.calls.append(text)
        if self.fail_at is not None and len(self.calls) == self.fail_at:
            raise RuntimeError("synthesis failed")
        return bytes((self.marker, 0)) * (240 + len(self.calls))


def _config(language: str = "auto") -> Config:
    config = Config()
    config.voice.language = language
    config.voice.tts_engine = "remote"
    config.voice.tts_endpoint = "http://voice.invalid"
    config.voice.tts_voice = "richard"
    return config


def _cache(tmp_path: Path) -> Path:
    return tmp_path / "realtime-cues"


@pytest.mark.parametrize(
    ("configured", "selected", "first_thinking", "first_visual"),
    [
        ("auto", "en", "Mm, let me think.", "Let me take a look."),
        ("English", "en", "Mm, let me think.", "Let me take a look."),
        ("it", "it", "Mmh, fammi pensare.", "Fammi dare un'occhiata."),
        ("Italian", "it", "Mmh, fammi pensare.", "Fammi dare un'occhiata."),
    ],
)
def test_prepare_selects_configured_catalog_and_returns_six_pcm16_clips(
    tmp_path, configured, selected, first_thinking, first_visual
):
    synth = FakeSynth()

    bank = prepare_cues(_config(configured), _cache(tmp_path), synth=synth)

    assert bank["language"] == selected
    assert bank["fingerprint"]
    assert [clip["id"] for clip in bank["clips"]] == [
        "thinking-0", "thinking-1", "thinking-2", "visual-0", "visual-1", "visual-2"
    ]
    assert [clip["phase"] for clip in bank["clips"]].count("thinking") == 3
    assert [clip["phase"] for clip in bank["clips"]].count("visual") == 3
    assert bank["clips"][0]["text"] == first_thinking
    assert bank["clips"][3]["text"] == first_visual
    assert all(clip["sample_rate"] == 24000 for clip in bank["clips"])
    assert all(len(base64.b64decode(clip["audio"])) % 2 == 0 for clip in bank["clips"])
    assert synth.calls == [clip["text"] for clip in bank["clips"]]


def test_unsupported_explicit_language_returns_empty_without_building_tts(tmp_path):
    class ForbiddenSynth:
        @property
        def samplerate(self):
            raise AssertionError("unsupported language must not inspect TTS")

        def synth(self, text):
            raise AssertionError("unsupported language must not synthesize")

    bank = prepare_cues(_config("fr"), _cache(tmp_path), synth=ForbiddenSynth())

    assert bank == {
        "fingerprint": cue_fingerprint(_config("fr"), "fr"),
        "language": "fr",
        "clips": [],
    }
    assert not _cache(tmp_path).exists()


def test_valid_bank_is_reused_without_synthesis(tmp_path):
    config = _config()
    first = prepare_cues(config, _cache(tmp_path), synth=FakeSynth(marker=3))

    second = prepare_cues(
        config,
        _cache(tmp_path),
        synth=lambda: (_ for _ in ()).throw(AssertionError("must reuse cache")),
    )

    assert second == first


@pytest.mark.parametrize(
    ("field", "changed"),
    [
        ("tts_engine", "piper"),
        ("tts_voice", "replacement"),
        ("tts_endpoint", "https://other.invalid"),
        ("tts_model", "other-model"),
        ("tts_language", "Italian"),
        ("tts_instructions", "speak softly"),
        ("tts_xvec_only", True),
        ("tts_task_type", "Base"),
        ("tts_streaming", False),
        ("tts_effect", "robot"),
        ("tts_effect_strength", 75),
        ("tts_effect_tone", 80.0),
    ],
)
def test_fingerprint_changes_for_every_tts_setting(field, changed):
    config = _config()
    before = cue_fingerprint(config, "en")

    setattr(config.voice, field, changed)

    assert cue_fingerprint(config, "en") != before


def test_read_rejects_bank_after_voice_effect_or_language_change(tmp_path):
    cache = _cache(tmp_path)
    original = _config("en")
    prepared = prepare_cues(original, cache, synth=FakeSynth())

    for field, value in (("tts_voice", "new-voice"), ("tts_effect", "robot")):
        changed = _config("en")
        setattr(changed.voice, field, value)
        bank = read_cues(changed, cache)
        assert bank["fingerprint"] != prepared["fingerprint"]
        assert bank["language"] == "en"
        assert bank["clips"] == []

    italian = read_cues(_config("it"), cache)
    assert italian["fingerprint"] != prepared["fingerprint"]
    assert italian["language"] == "it"
    assert italian["clips"] == []


def test_manifest_contains_no_endpoint_or_credentials(tmp_path):
    config = _config()
    config.voice.tts_endpoint = "https://user:super-secret@voice.internal.example"

    prepare_cues(config, _cache(tmp_path), synth=FakeSynth())

    manifest = (_cache(tmp_path) / "manifest.json").read_text(encoding="utf-8")
    assert "super-secret" not in manifest
    assert "voice.internal.example" not in manifest


@pytest.mark.parametrize(
    "corrupt",
    [
        lambda data: data["clips"][0].__setitem__("audio", ""),
        lambda data: data["clips"][0].__setitem__("audio", "AQ=="),
        lambda data: data["clips"][0].__setitem__("sample_rate", 0),
        lambda data: data["clips"][0].__setitem__("byte_length", 999),
        lambda data: data["clips"][0].__setitem__("sha256", "0" * 64),
        lambda data: data["clips"].pop(),
    ],
)
def test_read_rejects_corrupt_or_incomplete_bank_as_a_whole(tmp_path, corrupt):
    config = _config()
    cache = _cache(tmp_path)
    expected = prepare_cues(config, cache, synth=FakeSynth())
    path = cache / "manifest.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    corrupt(data)
    path.write_text(json.dumps(data), encoding="utf-8")

    bank = read_cues(config, cache)

    assert bank == {
        "fingerprint": expected["fingerprint"],
        "language": "en",
        "clips": [],
    }


def test_read_rejects_invalid_json_and_missing_bank(tmp_path):
    config = _config()
    empty = read_cues(config, _cache(tmp_path))
    assert empty["clips"] == []

    cache = _cache(tmp_path)
    cache.mkdir()
    (cache / "manifest.json").write_text("{broken", encoding="utf-8")
    assert read_cues(config, cache) == empty


def test_failed_forced_preparation_preserves_the_complete_previous_bank(tmp_path):
    config = _config()
    cache = _cache(tmp_path)
    first = prepare_cues(config, cache, synth=FakeSynth(marker=5))

    with pytest.raises(RuntimeError, match="synthesis failed"):
        prepare_cues(config, cache, synth=FakeSynth(marker=9, fail_at=3), force=True)

    assert read_cues(config, cache) == first
    assert sorted(path.name for path in cache.iterdir()) == ["manifest.json"]


def test_force_atomically_replaces_bank_for_same_voice_identifier(tmp_path):
    config = _config()
    cache = _cache(tmp_path)
    first = prepare_cues(config, cache, synth=FakeSynth(marker=5))

    second = prepare_cues(config, cache, synth=FakeSynth(marker=9), force=True)

    assert second["fingerprint"] == first["fingerprint"]
    assert second["clips"][0]["audio"] != first["clips"][0]["audio"]
    assert read_cues(config, cache) == second


def test_prepare_rejects_unreasonably_long_pcm(tmp_path):
    class LongSynth:
        samplerate = 24000

        def synth(self, text):
            return b"\x00\x00" * (self.samplerate * 30 + 1)

    with pytest.raises(ValueError, match="invalid PCM16"):
        prepare_cues(_config(), _cache(tmp_path), synth=LongSynth())

    assert not _cache(tmp_path).exists()


def test_http_get_reads_adjacent_custom_config_cache_without_synthesis(
    tmp_path, monkeypatch
):
    config_path = tmp_path / "custom" / "config.toml"
    config = _config("it")
    save_config(config, config_path)
    prepared = prepare_cues(
        config, config_path.parent / "realtime-cues", synth=FakeSynth(marker=7)
    )
    monkeypatch.setattr(
        "richard.cli._build_tts",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("HTTP must not synthesize")),
    )
    app = WebApp(
        config_path=config_path,
        memory_store=MemoryStore(":memory:"),
        control_loop_store=ControlLoopStore(":memory:"),
        relays=RelayRegistry(),
    )

    response = app.handle("GET", "/api/realtime/cues")

    assert response.status == 200
    assert json.loads(response.body) == prepared


def test_cli_prepare_uses_build_tts_and_cache_adjacent_to_selected_config(
    tmp_path, monkeypatch, capsys
):
    config_path = tmp_path / "profile" / "richard.toml"
    config = _config("en")
    save_config(config, config_path)
    synth = FakeSynth(marker=4)
    monkeypatch.setattr("richard.cli._build_tts", lambda loaded, write: synth)

    assert main(["prepare", "--config", str(config_path)]) == 0

    bank = read_cues(config, config_path.parent / "realtime-cues")
    assert len(bank["clips"]) == 6
    assert synth.calls
    assert "Prepared 6 realtime voice cues (en)." in capsys.readouterr().out
