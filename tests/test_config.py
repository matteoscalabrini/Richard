import stat

import pytest

from richard.config import Config, HomeAssistant, Personality, clamp_dial, default_plugins_dir, load_config, save_config


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


def test_home_assistant_view_defaults():
    config = Config()
    assert config.home_assistant.enabled is False
    assert config.home_assistant.host == "homeassistant.local"
    assert "home_assistant" not in config.plugins.tables


def test_set_home_assistant_writes_the_plugin_table(tmp_path):
    config = Config()
    settings = HomeAssistant(enabled=True, host="ha.local", port=9443, use_https=True, token="t", timeout=5.0, verify_ssl=False)
    config.set_home_assistant(settings)
    assert config.plugins.enabled == ["home_assistant"]
    assert config.plugins.tables["home_assistant"] == {
        "host": "ha.local", "port": 9443, "use_https": True, "token": "t", "timeout": 5.0, "verify_ssl": False,
    }
    path = tmp_path / "config.toml"
    save_config(config, path)
    text = path.read_text()
    assert "[plugins.home_assistant]" in text
    assert "[home_assistant]" not in text
    again = load_config(path)
    assert again.home_assistant == settings
    settings.enabled = False
    again.set_home_assistant(settings)
    assert again.plugins.enabled == []


def test_legacy_home_assistant_table_is_migrated_and_dropped_on_save(tmp_path):
    path = tmp_path / "config.toml"
    path.write_text('[home_assistant]\nenabled = true\nurl = "https://ha-old.example:9443/api"\ntoken = "legacy"\n')
    config = load_config(path)
    assert config.plugins.enabled == ["home_assistant"]
    assert config.home_assistant.host == "ha-old.example"
    assert config.home_assistant.port == 9443
    assert config.home_assistant.use_https is True
    assert config.home_assistant.token == "legacy"
    save_config(config, path)
    assert "[home_assistant]" not in path.read_text()
    assert load_config(path).home_assistant.host == "ha-old.example"


def test_home_assistant_env_overrides_write_the_plugin_table(monkeypatch, tmp_path):
    path = tmp_path / "config.toml"
    path.write_text("")
    monkeypatch.setenv("RICHARD_HA_ENABLED", "on")
    monkeypatch.setenv("RICHARD_HA_URL", "https://ha.example:9443")
    monkeypatch.setenv("RICHARD_HA_TOKEN", "env-token")
    config = load_config(path)
    assert config.plugins.enabled == ["home_assistant"]
    assert config.home_assistant.url == "https://ha.example:9443"
    assert config.home_assistant.token == "env-token"
    monkeypatch.setenv("RICHARD_HA_ENABLED", "off")
    assert load_config(path).plugins.enabled == []


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


def test_chatterbox_knobs_are_gone_and_ignored_in_old_files(tmp_path):
    for name in ("tts_exaggeration", "tts_cfg_weight", "tts_temperature", "tts_speed"):
        assert not hasattr(Config().voice, name)
    path = tmp_path / "config.toml"
    path.write_text('[voice]\ntts_exaggeration = 1.2\ntts_speed = 1.3\ntts_voice = "clap1"\n')
    assert load_config(path).voice.tts_voice == "clap1"


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


def test_voice_remote_model_language_instructions_defaults():
    from richard.config import Voice

    v = Voice()
    assert v.tts_model == "chatterbox"
    assert v.tts_language == ""
    assert v.tts_instructions == ""


def test_voice_remote_model_language_instructions_roundtrip(tmp_path):
    from richard.config import Config, load_config, save_config

    path = tmp_path / "config.toml"
    config = Config()
    config.voice.tts_model = ""
    config.voice.tts_language = "Italian"
    config.voice.tts_instructions = "dry, deadpan, slightly amused"
    save_config(config, path)
    loaded = load_config(path)
    assert loaded.voice.tts_model == ""
    assert loaded.voice.tts_language == "Italian"
    assert loaded.voice.tts_instructions == "dry, deadpan, slightly amused"


def test_voice_tts_xvec_only_defaults_false_and_roundtrips(tmp_path):
    from richard.config import Config, Voice, load_config, save_config

    assert Voice().tts_xvec_only is False
    path = tmp_path / "config.toml"
    config = Config()
    config.voice.tts_xvec_only = True
    save_config(config, path)
    assert load_config(path).voice.tts_xvec_only is True


def test_voice_tts_task_type_defaults_blank_and_roundtrips(tmp_path):
    from richard.config import Config, Voice, load_config, save_config

    assert Voice().tts_task_type == ""
    path = tmp_path / "config.toml"
    config = Config()
    config.voice.tts_task_type = "Base"
    save_config(config, path)
    assert load_config(path).voice.tts_task_type == "Base"


def test_llm_extra_body_roundtrip_and_role_fallback(tmp_path):
    from richard.config import BrainRole, Config, load_config, resolve_brain_role, save_config

    assert Config().llm_extra_body == {}
    assert BrainRole().extra_body is None
    path = tmp_path / "config.toml"
    config = Config()
    config.llm_extra_body = {"chat_template_kwargs": {"enable_thinking": False}}
    config.brains["voice"] = BrainRole(extra_body={"chat_template_kwargs": {"reasoning_effort": "medium"}})
    config.brains["curator"] = BrainRole(model="small")
    save_config(config, path)
    loaded = load_config(path)
    assert loaded.llm_extra_body == {"chat_template_kwargs": {"enable_thinking": False}}
    assert resolve_brain_role(loaded, "voice").extra_body == {"chat_template_kwargs": {"reasoning_effort": "medium"}}
    # a role without its own extra_body inherits the top-level one
    assert resolve_brain_role(loaded, "curator").extra_body == {"chat_template_kwargs": {"enable_thinking": False}}


def test_plugins_table_round_trips_unknown_names(tmp_path):
    path = tmp_path / "config.toml"
    path.write_text(
        '[plugins]\nenabled = ["home_assistant", "mystery"]\n\n'
        '[plugins.home_assistant]\nhost = "ha.local"\nport = 8123\n\n'
        '[plugins.mystery]\nlevel = 3\nflag = true\n'
    )
    config = load_config(path)
    assert config.plugins.enabled == ["home_assistant", "mystery"]
    assert config.plugins.tables["mystery"] == {"level": 3, "flag": True}
    save_config(config, path)
    again = load_config(path)
    assert again.plugins.enabled == ["home_assistant", "mystery"]
    assert again.plugins.tables == {"home_assistant": {"host": "ha.local", "port": 8123}, "mystery": {"level": 3, "flag": True}}


def test_plugins_default_empty_and_table_creates_on_demand():
    config = Config()
    assert config.plugins.enabled == []
    assert config.plugins.tables == {}
    config.plugins.table("reachy")["host"] = "10.99.77.5"
    assert config.plugins.tables == {"reachy": {"host": "10.99.77.5"}}


def test_default_plugins_dir(monkeypatch, tmp_path):
    monkeypatch.setenv("HOME", str(tmp_path))
    assert default_plugins_dir() == tmp_path / ".richard" / "plugins"


def test_home_assistant_from_table_and_to_table():
    from richard.config import HomeAssistant

    settings = HomeAssistant.from_table({"host": "ha.local", "port": 9443, "use_https": True, "token": "t"}, enabled=True)
    assert settings.enabled is True
    assert settings.url == "https://ha.local:9443"
    assert settings.timeout == 10.0  # default kept
    assert settings.to_table() == {
        "host": "ha.local", "port": 9443, "use_https": True, "token": "t", "timeout": 10.0, "verify_ssl": True,
    }
    assert HomeAssistant.from_table({}).to_table() == {
        "host": "homeassistant.local", "port": 8123, "use_https": False, "timeout": 10.0, "verify_ssl": True,
    }


def test_voice_effect_defaults_and_roundtrip(tmp_path):
    cfg = Config()
    assert (cfg.voice.tts_effect, cfg.voice.tts_effect_strength, cfg.voice.tts_effect_tone) == ("none", 50, 40.0)
    cfg.voice.tts_effect = "robot"
    cfg.voice.tts_effect_strength = 70
    cfg.voice.tts_effect_tone = 55.0
    path = tmp_path / "config.toml"
    save_config(cfg, path)
    v = load_config(path).voice
    assert (v.tts_effect, v.tts_effect_strength, v.tts_effect_tone) == ("robot", 70, 55.0)


def test_voice_effect_clamped_on_load(tmp_path):
    path = tmp_path / "config.toml"
    path.write_text('[voice]\ntts_effect = "robot"\ntts_effect_strength = 250\ntts_effect_tone = 5\n')
    v = load_config(path).voice
    assert v.tts_effect_strength == 100
    assert v.tts_effect_tone == 20.0
