import stat

import pytest

from richard.config import Config, HomeAssistant, Personality, clamp_dial, load_config, save_config


def test_load_returns_defaults_when_missing(tmp_path):
    config = load_config(tmp_path / "nope.toml")
    assert config.llm_endpoint == "http://localhost:8080"
    assert config.llm_model == "local"
    assert config.llm_api_key is None


def test_save_then_load_roundtrip(tmp_path):
    path = tmp_path / "config.toml"
    save_config(Config(llm_endpoint="http://box:8080", llm_model="qwen"), path)
    loaded = load_config(path)
    assert loaded.llm_endpoint == "http://box:8080"
    assert loaded.llm_model == "qwen"
    assert stat.S_IMODE(path.stat().st_mode) == 0o600


def test_env_overrides_file(tmp_path, monkeypatch):
    path = tmp_path / "config.toml"
    save_config(Config(llm_endpoint="http://box:8080"), path)
    monkeypatch.setenv("RICHARD_LLM_ENDPOINT", "http://override:9000")
    loaded = load_config(path)
    assert loaded.llm_endpoint == "http://override:9000"


def test_personality_defaults():
    p = Config().personality
    assert (p.name, p.humour, p.honesty, p.directness) == ("Richard", 70, 90, 60)


def test_personality_roundtrip(tmp_path):
    path = tmp_path / "config.toml"
    save_config(
        Config(personality=Personality(name="Tars", humour=100, honesty=85, directness=40)),
        path,
    )
    loaded = load_config(path)
    assert loaded.personality.name == "Tars"
    assert loaded.personality.humour == 100
    assert loaded.personality.honesty == 85
    assert loaded.personality.directness == 40


def test_personality_clamped_on_load(tmp_path):
    path = tmp_path / "config.toml"
    path.write_text("[personality]\nhumour = 150\nhonesty = -5\ndirectness = 60\n")
    loaded = load_config(path)
    assert loaded.personality.humour == 100
    assert loaded.personality.honesty == 0
    assert loaded.personality.directness == 60


def test_missing_personality_section_uses_defaults(tmp_path):
    path = tmp_path / "config.toml"
    path.write_text('llm_endpoint = "http://x:8080"\n')
    loaded = load_config(path)
    assert loaded.personality.name == "Richard"
    assert loaded.personality.humour == 70


@pytest.mark.parametrize(
    "raw,expected",
    [(150, 100), (-5, 0), (50, 50), ("80", 80), ("bad", 0), (None, 0)],
)
def test_clamp_dial(raw, expected):
    assert clamp_dial(raw) == expected


def test_llm_timeout_default():
    assert Config().llm_timeout == 180.0


def test_llm_timeout_roundtrip(tmp_path):
    path = tmp_path / "config.toml"
    save_config(Config(llm_timeout=240.0), path)
    assert load_config(path).llm_timeout == 240.0


def test_llm_timeout_env_override(tmp_path, monkeypatch):
    path = tmp_path / "config.toml"
    save_config(Config(llm_timeout=120.0), path)
    monkeypatch.setenv("RICHARD_LLM_TIMEOUT", "30")
    assert load_config(path).llm_timeout == 30.0


def test_home_assistant_defaults():
    ha = Config().home_assistant
    assert ha.enabled is False
    assert ha.host == "homeassistant.local"
    assert ha.port == 8123
    assert ha.url == "http://homeassistant.local:8123"
    assert ha.token is None
    assert ha.timeout == 10.0
    assert ha.verify_ssl is True


def test_home_assistant_roundtrip(tmp_path):
    path = tmp_path / "config.toml"
    save_config(
        Config(
            home_assistant=HomeAssistant(
                enabled=True,
                host="ha.example.test",
                port=8443,
                use_https=True,
                token="secret",
                timeout=20.0,
                verify_ssl=False,
            )
        ),
        path,
    )
    ha = load_config(path).home_assistant
    assert ha.enabled is True
    assert ha.host == "ha.example.test"
    assert ha.port == 8443
    assert ha.url == "https://ha.example.test:8443"
    assert ha.token == "secret"
    assert ha.timeout == 20.0
    assert ha.verify_ssl is False


def test_home_assistant_env_overrides(tmp_path, monkeypatch):
    monkeypatch.setenv("RICHARD_HA_ENABLED", "on")
    monkeypatch.setenv("RICHARD_HA_URL", "http://ha-env:8123")
    monkeypatch.setenv("RICHARD_HA_TOKEN", "env-secret")
    ha = load_config(tmp_path / "missing.toml").home_assistant
    assert ha.enabled is True
    assert ha.host == "ha-env"
    assert ha.port == 8123
    assert ha.url == "http://ha-env:8123"
    assert ha.token == "env-secret"


def test_home_assistant_loads_legacy_url_config(tmp_path):
    path = tmp_path / "config.toml"
    path.write_text(
        '[home_assistant]\nenabled = true\nurl = "https://ha-old.example:9443/api"\n'
    )
    ha = load_config(path).home_assistant
    assert ha.host == "ha-old.example"
    assert ha.port == 9443
    assert ha.use_https is True
    assert ha.url == "https://ha-old.example:9443"


def test_voice_config_roundtrips(tmp_path):
    from richard.config import Config, load_config, save_config

    cfg = Config()
    cfg.voice.stt_model = "small.en"
    cfg.voice.tts_engine = "piper"
    cfg.voice.tts_voice = "TARS"
    cfg.voice.vad_aggressiveness = 3
    cfg.voice.silence_ms = 600
    path = tmp_path / "config.toml"
    save_config(cfg, path)
    loaded = load_config(path)
    assert loaded.voice.stt_model == "small.en"
    assert loaded.voice.tts_engine == "piper"
    assert loaded.voice.tts_voice == "TARS"
    assert loaded.voice.vad_aggressiveness == 3
    assert loaded.voice.silence_ms == 600


def test_voice_defaults_when_section_absent(tmp_path):
    from richard.config import load_config

    path = tmp_path / "config.toml"
    path.write_text('llm_model = "Gemma4"\n')
    loaded = load_config(path)
    assert loaded.voice.stt_model == "base.en"
    assert loaded.voice.tts_engine == "kokoro"
    assert loaded.voice.tts_voice == "bm_lewis"
    assert loaded.voice.samplerate == 16000


def test_system_prompt_defaults_empty():
    assert Config().personality.system_prompt == ""


def test_system_prompt_roundtrip(tmp_path):
    path = tmp_path / "config.toml"
    cfg = Config()
    cfg.personality.system_prompt = "You are a lighthouse keeper."
    save_config(cfg, path)
    assert load_config(path).personality.system_prompt == "You are a lighthouse keeper."


def test_voice_tts_tuning_defaults():
    v = Config().voice
    assert v.tts_exaggeration == 0.4
    assert v.tts_cfg_weight == 0.5
    assert v.tts_temperature == 0.8
    assert v.tts_speed == 1.0


def test_voice_tts_tuning_roundtrip(tmp_path):
    path = tmp_path / "config.toml"
    cfg = Config()
    cfg.voice.tts_exaggeration = 1.2
    cfg.voice.tts_cfg_weight = 0.7
    cfg.voice.tts_temperature = 0.5
    cfg.voice.tts_speed = 1.3
    save_config(cfg, path)
    v = load_config(path).voice
    assert (v.tts_exaggeration, v.tts_cfg_weight, v.tts_temperature, v.tts_speed) == (1.2, 0.7, 0.5, 1.3)


def test_brains_default_empty_and_resolve_falls_back_to_llm_settings():
    from richard.config import BrainRole, resolve_brain_role

    config = Config(llm_endpoint="http://gpu:8080", llm_model="gemma", llm_api_key="k", llm_timeout=99.0)
    assert config.brains == {}
    for role in ("conversational", "curator", "escalation"):
        resolved = resolve_brain_role(config, role)
        assert resolved == BrainRole(endpoint="http://gpu:8080", model="gemma", api_key="k", timeout=99.0)


def test_brains_roundtrip_and_partial_role_inherits_missing_fields(tmp_path):
    from richard.config import BrainRole, resolve_brain_role

    path = tmp_path / "config.toml"
    config = Config(llm_endpoint="http://local:8080", llm_model="small")
    config.brains["curator"] = BrainRole(endpoint="http://gpu:9090", model="big")
    config.brains["escalation"] = BrainRole(model="big")  # endpoint inherited
    save_config(config, path)
    loaded = load_config(path)
    assert loaded.brains["curator"] == BrainRole(endpoint="http://gpu:9090", model="big")
    curator = resolve_brain_role(loaded, "curator")
    assert (curator.endpoint, curator.model, curator.timeout) == ("http://gpu:9090", "big", 180.0)
    escalation = resolve_brain_role(loaded, "escalation")
    assert (escalation.endpoint, escalation.model) == ("http://local:8080", "big")


def test_role_chains_through_conversational_before_top_level():
    from richard.config import BrainRole, resolve_brain_role

    config = Config(llm_endpoint="http://old:8080", llm_model="old")
    config.brains["conversational"] = BrainRole(endpoint="http://new:8080", model="new")
    curator = resolve_brain_role(config, "curator")
    assert (curator.endpoint, curator.model) == ("http://new:8080", "new")


def test_config_without_brains_table_saves_without_brains_key(tmp_path):
    path = tmp_path / "config.toml"
    save_config(Config(), path)
    assert "brains" not in path.read_text()


def test_realtime_and_language_roundtrip(tmp_path):
    path = tmp_path / "config.toml"
    config = load_config(path)
    assert config.realtime.enabled is True
    assert config.realtime.port == 8766
    assert config.realtime.token == ""
    assert config.voice.language == "auto"
    assert config.voice.endpoint_silence_ms == 400
    config.realtime.enabled = False
    config.realtime.port = 9001
    config.realtime.token = "sekrit"
    config.voice.language = "it"
    config.voice.endpoint_silence_ms = 300
    save_config(config, path)
    loaded = load_config(path)
    assert loaded.realtime.enabled is False
    assert loaded.realtime.port == 9001
    assert loaded.realtime.token == "sekrit"
    assert loaded.voice.language == "it"
    assert loaded.voice.endpoint_silence_ms == 300
