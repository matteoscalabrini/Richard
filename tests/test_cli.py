import pytest

from richard import cli


def test_version_flag_exits_zero(capsys):
    with pytest.raises(SystemExit) as exc:
        cli.main(["--version"])
    assert exc.value.code == 0
    assert "richard" in capsys.readouterr().out


def test_config_show_reflects_set(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("HOME", str(tmp_path))  # default_config_path() -> tmp/.richard/config.toml
    monkeypatch.delenv("RICHARD_LLM_ENDPOINT", raising=False)
    cli.main(["config", "--set-endpoint", "http://box:8080"])
    capsys.readouterr()  # clear "Saved." output
    cli.main(["config", "--show"])
    out = capsys.readouterr().out
    assert "http://box:8080" in out


def test_config_set_humor_clamps_and_shows(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.delenv("RICHARD_LLM_ENDPOINT", raising=False)
    cli.main(["config", "--set-humor", "150"])
    capsys.readouterr()
    cli.main(["config", "--show"])
    out = capsys.readouterr().out
    assert "humour:" in out
    assert "100" in out  # 150 clamped to 100


def test_config_set_name_empty_is_ignored(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.delenv("RICHARD_LLM_ENDPOINT", raising=False)
    cli.main(["config", "--set-name", ""])
    capsys.readouterr()
    cli.main(["config", "--show"])
    out = capsys.readouterr().out
    assert "name:" in out and "Richard" in out


def test_config_set_timeout_shows(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.delenv("RICHARD_LLM_TIMEOUT", raising=False)
    cli.main(["config", "--set-timeout", "240"])
    capsys.readouterr()
    cli.main(["config", "--show"])
    out = capsys.readouterr().out
    assert "timeout:" in out
    assert "240" in out


def test_run_chat_builds_engine(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    captured = {}

    def fake_run_repl(engine, conversation, *args, **kwargs):
        captured["engine"] = engine
        captured["history"] = conversation.history()

    monkeypatch.setattr(cli, "run_repl", fake_run_repl)
    cli.main([])  # no subcommand -> _run_chat
    from richard.engine import Engine

    assert isinstance(captured["engine"], Engine)
    assert captured["history"] == []


def test_memory_list_and_forget(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("HOME", str(tmp_path))
    from richard.memory import MemoryStore, default_memory_path

    store = MemoryStore(default_memory_path())
    m = store.add("likes tea")
    store.close()

    cli.main(["memory"])
    assert f"[{m.id}] likes tea" in capsys.readouterr().out

    cli.main(["memory", "forget", str(m.id)])
    assert "Forgotten" in capsys.readouterr().out

    cli.main(["memory"])
    assert "No memories yet." in capsys.readouterr().out


def test_voice_subcommand_is_registered():
    from richard.cli import _build_parser

    args = _build_parser().parse_args(["voice"])
    assert args.command == "voice"


def test_run_voice_reports_missing_extras(monkeypatch):
    from richard import cli

    monkeypatch.setattr(cli, "_voice_deps_available", lambda *a, **k: "sounddevice")
    writes = []
    rc = cli._run_voice(write=writes.append)
    assert rc == 1
    assert any("Voice extras not installed" in w for w in writes)


def test_config_sets_voice_fields(tmp_path, monkeypatch):
    from richard import cli
    from richard.config import load_config

    path = tmp_path / "config.toml"
    monkeypatch.setattr("richard.config.default_config_path", lambda: path)
    args = cli._build_parser().parse_args(
        [
            "config",
            "--set-voice-model", "small.en",
            "--set-voice-engine", "piper",
            "--set-voice-name", "TARS",
            "--set-vad", "3",
        ]
    )
    cli._run_config(args, write=lambda s: None)
    loaded = load_config(path)
    assert loaded.voice.stt_model == "small.en"
    assert loaded.voice.tts_engine == "piper"
    assert loaded.voice.tts_voice == "TARS"
    assert loaded.voice.vad_aggressiveness == 3


def test_config_sets_remote_voice_fields(tmp_path, monkeypatch):
    from richard import cli
    from richard.config import load_config

    path = tmp_path / "config.toml"
    monkeypatch.setattr("richard.config.default_config_path", lambda: path)
    args = cli._build_parser().parse_args(
        [
            "config",
            "--set-voice-engine", "remote",
            "--set-tts-endpoint", "http://gpu:8004",
            "--set-stt-engine", "remote",
            "--set-stt-endpoint", "http://gpu:8005",
        ]
    )
    cli._run_config(args, write=lambda s: None)
    v = load_config(path).voice
    assert v.tts_engine == "remote" and v.tts_endpoint == "http://gpu:8004"
    assert v.stt_engine == "remote" and v.stt_endpoint == "http://gpu:8005"


def test_config_sets_home_assistant_fields(tmp_path, monkeypatch):
    from richard import cli
    from richard.config import load_config

    path = tmp_path / "config.toml"
    monkeypatch.setattr("richard.config.default_config_path", lambda: path)
    args = cli._build_parser().parse_args(
        [
            "config",
            "--set-ha-enabled", "on",
            "--set-ha-host", "ha.local",
            "--set-ha-port", "8443",
            "--set-ha-https", "on",
            "--set-ha-token", "secret",
            "--set-ha-timeout", "20",
            "--set-ha-verify-ssl", "off",
        ]
    )
    cli._run_config(args, write=lambda s: None)
    ha = load_config(path).home_assistant
    assert ha.enabled is True
    assert ha.host == "ha.local"
    assert ha.port == 8443
    assert ha.url == "https://ha.local:8443"
    assert ha.token == "secret"
    assert ha.timeout == 20.0
    assert ha.verify_ssl is False
    assert load_config(path).plugins.enabled == ["home_assistant"]


def test_build_plugins_reports_a_plugin_that_cannot_build(tmp_path, monkeypatch):
    from richard.config import Config, HomeAssistant

    monkeypatch.setenv("HOME", str(tmp_path))
    config = Config()
    config.set_home_assistant(HomeAssistant(enabled=True, host="", token=None))
    lines = []
    registry = cli._build_plugins(config, lines.append)
    assert lines == ["Plugin home_assistant disabled (host or token unset; configure both before restarting Richard)"]
    assert registry.providers() == []


def test_build_plugins_wires_home_assistant_when_configured(tmp_path, monkeypatch):
    from richard.config import Config, HomeAssistant

    monkeypatch.setenv("HOME", str(tmp_path))
    config = Config()
    config.set_home_assistant(HomeAssistant(enabled=True, host="ha.local", token="t"))
    registry = cli._build_plugins(config, lambda s: None)
    assert [type(p).__name__ for p in registry.providers()] == ["HomeAssistantProvider"]
    assert list(registry.target_readers()) == ["ha"]
    assert registry.context_lines() == ["Home Assistant is connected at http://ha.local:8123."]


def test_run_voice_errors_on_remote_without_endpoint(monkeypatch):
    from richard import cli
    from richard.config import Config

    cfg = Config()
    cfg.voice.tts_engine = "remote"
    cfg.voice.tts_endpoint = ""
    monkeypatch.setattr(cli, "load_config", lambda: cfg)
    monkeypatch.setattr(cli, "_voice_deps_available", lambda *a, **k: None)
    writes = []
    rc = cli._run_voice(write=writes.append)
    assert rc == 1
    assert any("tts_endpoint" in w for w in writes)


def test_voice_deps_guard_is_engine_selective(monkeypatch):
    import builtins

    from richard import cli

    real = builtins.__import__

    def fake(name, *a, **k):
        if name in ("faster_whisper", "kokoro_onnx", "piper.voice"):
            raise ImportError(name)
        if name in ("sounddevice", "webrtcvad"):
            return object()
        return real(name, *a, **k)

    monkeypatch.setattr(builtins, "__import__", fake)
    # Full remote: heavy model deps are never probed -> only audio I/O needed.
    assert cli._voice_deps_available("remote", "remote") is None
    # Local STT still needs faster-whisper; kokoro TTS still needs kokoro_onnx.
    assert cli._voice_deps_available("local", "remote") == "faster_whisper"
    assert cli._voice_deps_available("remote", "kokoro") == "kokoro_onnx"


# --- diagnostics wiring ---


def _providers_of(engine):
    return [type(p).__name__ for p in engine._providers]


def test_run_chat_engine_includes_diagnostics_when_ha_is_configured(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("RICHARD_HA_ENABLED", "on")
    monkeypatch.setenv("RICHARD_HA_HOST", "ha.local")
    monkeypatch.setenv("RICHARD_HA_TOKEN", "token")
    captured = {}
    monkeypatch.setattr(
        cli, "run_repl", lambda engine, convo, *a, **k: captured.update(engine=engine)
    )
    cli.main([])
    provider_names = _providers_of(captured["engine"])
    assert "HomeAssistantProvider" in provider_names
    assert "DiagnosticsProvider" in provider_names
    diagnostics = captured["engine"]._providers[provider_names.index("DiagnosticsProvider")]
    names = [s["function"]["name"] for s in diagnostics.schemas()]
    assert names == ["refresh_devices", "diagnose_target", "verify_target_state"]


def test_run_chat_engine_skips_diagnostics_without_home_assistant(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    captured = {}
    monkeypatch.setattr(
        cli, "run_repl", lambda engine, convo, *a, **k: captured.update(engine=engine)
    )
    cli.main([])
    provider_names = _providers_of(captured["engine"])
    assert "DiagnosticsProvider" not in provider_names
    assert "ControlLoopProvider" in provider_names


def test_engine_providers_order():
    providers = cli._engine_providers(
        memory_provider="memory", plugin_providers=["ha", "reachy"], control_provider="loops", diagnostics="diagnostics",
    )
    assert providers == ["memory", "ha", "reachy", "loops", "diagnostics"]
    assert cli._engine_providers(memory_provider="memory", plugin_providers=[], control_provider="loops", diagnostics=None) == ["memory", "loops"]


def test_build_diagnostics_needs_at_least_one_reader():
    from richard.plugins.registry import PluginRegistry

    empty = PluginRegistry([])
    assert cli._build_diagnostics(empty) is None


def test_serve_engine_prompt_carries_plugin_context_lines(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("RICHARD_HA_ENABLED", "on")
    monkeypatch.setenv("RICHARD_HA_HOST", "ha.local")
    monkeypatch.setenv("RICHARD_HA_TOKEN", "token")
    captured = {}
    monkeypatch.setattr(cli, "run_repl", lambda engine, convo, *a, **k: captured.update(engine=engine))
    cli.main([])
    assert "Home Assistant is connected at http://ha.local:8123." in captured["engine"]._system_prompt()


def test_serve_realtime_guarded_swallows_bind_failure():
    import asyncio

    from richard.cli import _serve_realtime_guarded

    async def boom():
        raise OSError("address already in use")

    lines = []
    asyncio.run(_serve_realtime_guarded(boom(), lines.append))
    assert lines and "Realtime API disabled" in lines[0]


def test_realtime_session_factory_builds_wired_session():
    from richard.cli import _realtime_session_factory
    from richard.config import Config

    config = Config()
    config.voice.endpoint_silence_ms = 320

    class NullTTS:
        samplerate = 24000

        def synth(self, text):
            return b""

    class NullTranscriber:
        def partial(self, pcm):
            return ""

        def final(self, pcm):
            return ""

    class ScriptedVAD:
        def is_speech(self, frame, samplerate=16000):
            return False

        def reset(self):
            pass

    factory = _realtime_session_factory(
        config,
        brain=object(),
        providers_fn=lambda: [],
        synth=NullTTS(),
        transcriber=NullTranscriber(),
        vad_factory=ScriptedVAD,
    )
    emitted = []
    session = factory(emitted.append)
    try:
        assert session.samplerate == 24000
        assert session.barge_in == "vad"
        assert session.conversation.history() == []
    finally:
        session.close()


def test_build_tts_remote_passes_model_language_and_instructions(monkeypatch):
    import richard.voice.remote as remote_mod
    from richard.cli import _build_tts
    from richard.config import Config

    seen = {}

    class FakeRemoteTTS:
        def __init__(self, endpoint, voice, **kwargs):
            seen.update(endpoint=endpoint, voice=voice, **kwargs)

    monkeypatch.setattr(remote_mod, "RemoteTTS", FakeRemoteTTS)
    config = Config()
    config.voice.tts_engine = "remote"
    config.voice.tts_endpoint = "http://127.0.0.1:8091"
    config.voice.tts_voice = "richard"
    config.voice.tts_model = ""
    config.voice.tts_language = "Italian"
    config.voice.tts_instructions = "dry"
    _build_tts(config, lambda s: None)
    assert seen["endpoint"] == "http://127.0.0.1:8091"
    assert seen["voice"] == "richard"
    assert seen["model"] == ""
    assert seen["language"] == "Italian"
    assert seen["instructions"] == "dry"


def test_build_tts_remote_passes_x_vector_only(monkeypatch):
    import richard.voice.remote as remote_mod
    from richard.cli import _build_tts
    from richard.config import Config

    seen = {}

    class FakeRemoteTTS:
        def __init__(self, endpoint, voice, **kwargs):
            seen.update(kwargs)

    monkeypatch.setattr(remote_mod, "RemoteTTS", FakeRemoteTTS)
    config = Config()
    config.voice.tts_engine = "remote"
    config.voice.tts_endpoint = "http://127.0.0.1:8091"
    config.voice.tts_xvec_only = True
    _build_tts(config, lambda s: None)
    assert seen["x_vector_only"] is True


def test_build_tts_remote_passes_task_type(monkeypatch):
    import richard.voice.remote as remote_mod
    from richard.cli import _build_tts
    from richard.config import Config

    seen = {}

    class FakeRemoteTTS:
        def __init__(self, endpoint, voice, **kwargs):
            seen.update(kwargs)

    monkeypatch.setattr(remote_mod, "RemoteTTS", FakeRemoteTTS)
    config = Config()
    config.voice.tts_engine = "remote"
    config.voice.tts_endpoint = "http://127.0.0.1:8091"
    config.voice.tts_task_type = "Base"
    _build_tts(config, lambda s: None)
    assert seen["task_type"] == "Base"


def test_build_brain_passes_resolved_extra_body(monkeypatch):
    import richard.cli as cli_mod
    from richard.config import Config

    seen = {}

    class FakeBrain:
        def __init__(self, endpoint, model, api_key=None, **kwargs):
            seen.update(endpoint=endpoint, model=model, **kwargs)

    monkeypatch.setattr(cli_mod, "LlamaCppBrain", FakeBrain)
    config = Config()
    config.llm_extra_body = {"chat_template_kwargs": {"enable_thinking": False}}
    cli_mod._build_brain(config)
    assert seen["extra_body"] == {"chat_template_kwargs": {"enable_thinking": False}}
