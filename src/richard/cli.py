from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path
from typing import Callable

from richard import __version__
from richard.brain.llama_cpp import LlamaCppBrain
from richard.config import apply_home_assistant_url, clamp_dial, default_plugins_dir, load_config, save_config
from richard.conversation import Conversation
from richard.engine import Engine
from richard.memory import MemoryStore, default_memory_path
from richard.diagnostics import DiagnosticsProvider
from richard.providers.memory import MemoryProvider
from richard.repl import run_repl
from richard.setup import run_setup
from richard.setup.llm import configure_llm
from richard.setup.services import provision_voice_services
from richard.setup.verify import wait_for_endpoints


def _build_brain(config, role: str = "conversational") -> LlamaCppBrain:
    from richard.config import resolve_brain_role

    resolved = resolve_brain_role(config, role)
    return LlamaCppBrain(
        resolved.endpoint, resolved.model, resolved.api_key, timeout=resolved.timeout,
        extra_body=resolved.extra_body,
    )


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="richard", description="Self-hosted voice house companion"
    )
    parser.add_argument("--version", action="version", version=f"richard {__version__}")
    sub = parser.add_subparsers(dest="command")

    config_parser = sub.add_parser("config", help="View or edit configuration")
    config_parser.add_argument("--show", action="store_true", help="Print the current config")
    config_parser.add_argument("--set-endpoint", metavar="URL", help="Set the llama.cpp endpoint")
    config_parser.add_argument("--set-model", metavar="NAME", help="Set the model name")
    config_parser.add_argument(
        "--set-timeout", type=float, metavar="SECONDS", help="Set the request timeout in seconds"
    )
    config_parser.add_argument("--set-name", metavar="NAME", help="Set the companion's name")
    config_parser.add_argument("--set-humor", type=int, metavar="0-100", help="Set the humour dial")
    config_parser.add_argument("--set-honesty", type=int, metavar="0-100", help="Set the honesty dial")
    config_parser.add_argument(
        "--set-directness", type=int, metavar="0-100", help="Set the directness dial"
    )
    config_parser.add_argument("--set-voice-model", metavar="NAME", help="Set the local STT model (e.g. base.en)")
    config_parser.add_argument(
        "--set-voice-engine", choices=["kokoro", "piper", "remote"], help="Set the TTS engine"
    )
    config_parser.add_argument(
        "--set-voice-name", metavar="NAME", help="Set the TTS voice (bm_lewis / TARS / remote voice name)"
    )
    config_parser.add_argument("--set-tts-endpoint", metavar="URL", help="Set the remote TTS server URL")
    config_parser.add_argument(
        "--set-stt-engine", choices=["local", "remote"], help="Set the STT engine"
    )
    config_parser.add_argument("--set-stt-endpoint", metavar="URL", help="Set the remote STT server URL")
    config_parser.add_argument(
        "--set-tts-streaming", choices=["on", "off"],
        help="Speak sentence-by-sentence (on, low latency) or whole-utterance (off)",
    )
    config_parser.add_argument("--set-vad", type=int, metavar="0-3", help="Set the VAD aggressiveness")
    config_parser.add_argument(
        "--set-web-enabled", choices=["on", "off"], help="Enable or disable the config web UI on `serve`"
    )
    config_parser.add_argument("--set-web-host", metavar="HOST", help="Set the web UI bind host")
    config_parser.add_argument("--set-web-port", type=int, metavar="PORT", help="Set the web UI port")
    config_parser.add_argument(
        "--set-ha-enabled", choices=["on", "off"], help="Enable or disable Home Assistant"
    )
    config_parser.add_argument("--set-ha-url", metavar="URL", help="Set the Home Assistant URL")
    config_parser.add_argument(
        "--set-ha-host", metavar="IP_OR_HOST", help="Set the Home Assistant IP or hostname"
    )
    config_parser.add_argument(
        "--set-ha-port", type=int, metavar="PORT", help="Set the Home Assistant API port"
    )
    config_parser.add_argument(
        "--set-ha-https", choices=["on", "off"], help="Use HTTPS for Home Assistant"
    )
    config_parser.add_argument(
        "--set-ha-token", metavar="TOKEN", help="Set the Home Assistant long-lived access token"
    )
    config_parser.add_argument(
        "--set-ha-timeout", type=float, metavar="SECONDS", help="Set the Home Assistant timeout"
    )
    config_parser.add_argument(
        "--set-ha-verify-ssl", choices=["on", "off"],
        help="Enable or disable Home Assistant HTTPS certificate verification",
    )

    memory_parser = sub.add_parser("memory", help="Inspect or edit Richard's memory")
    memory_sub = memory_parser.add_subparsers(dest="memory_command")
    memory_sub.add_parser("list", help="List remembered facts")
    forget_parser = memory_sub.add_parser("forget", help="Delete a remembered fact by id")
    forget_parser.add_argument("id", type=int, help="The memory id to delete")

    sub.add_parser("voice", help="Talk to Richard (push-to-talk voice)")
    sub.add_parser("serve", help="Run the always-on host: voice satellites, realtime API, web UI")

    plugins_parser = sub.add_parser("plugins", help="List, enable, disable, configure or install plugins")
    plugins_sub = plugins_parser.add_subparsers(dest="plugins_command", required=True)
    plugins_sub.add_parser("list", help="Installed plugins and their state")
    plugins_sub.add_parser("enable", help="Enable a plugin").add_argument("name")
    plugins_sub.add_parser("disable", help="Disable a plugin").add_argument("name")
    config_p = plugins_sub.add_parser("config", help="Set plugin settings: key=value ...")
    config_p.add_argument("name")
    config_p.add_argument("pairs", nargs="+", metavar="key=value")
    plugins_sub.add_parser("install", help="pip install a plugin (folder = editable, else pypi name or git url)").add_argument("target")

    setup_parser = sub.add_parser("setup", help="Install/configure this box as a Richard brain")
    setup_parser.add_argument("--llm-url", metavar="URL", help="OpenAI-compatible LLM base URL")
    setup_parser.add_argument("--llm-key", metavar="KEY", help="LLM API key (cloud endpoints)")
    setup_parser.add_argument("--llm-model", metavar="NAME", help="LLM model name")
    setup_parser.add_argument(
        "--profile", choices=["cpu", "gpu"], help="Force a topology (hidden override; normally auto)"
    )
    setup_parser.add_argument(
        "--non-interactive", action="store_true", help="Never prompt; use flags/defaults"
    )
    setup_parser.add_argument(
        "--gpu", metavar="ID", default="0", help="CUDA device id for the voice services"
    )
    return parser


def _run_config(args: argparse.Namespace, write: Callable[[str], None] = print) -> int:
    config = load_config()
    changed = False
    if args.set_endpoint:
        config.llm_endpoint = args.set_endpoint
        changed = True
    if args.set_model:
        config.llm_model = args.set_model
        changed = True
    if args.set_timeout is not None:
        config.llm_timeout = args.set_timeout
        changed = True
    if args.set_name:
        config.personality.name = args.set_name
        changed = True
    if args.set_humor is not None:
        config.personality.humour = clamp_dial(args.set_humor)
        changed = True
    if args.set_honesty is not None:
        config.personality.honesty = clamp_dial(args.set_honesty)
        changed = True
    if args.set_directness is not None:
        config.personality.directness = clamp_dial(args.set_directness)
        changed = True
    if args.set_voice_model:
        config.voice.stt_model = args.set_voice_model
        changed = True
    if args.set_voice_engine:
        config.voice.tts_engine = args.set_voice_engine
        changed = True
    if args.set_voice_name:
        config.voice.tts_voice = args.set_voice_name
        changed = True
    if args.set_tts_endpoint:
        config.voice.tts_endpoint = args.set_tts_endpoint
        changed = True
    if args.set_stt_engine:
        config.voice.stt_engine = args.set_stt_engine
        changed = True
    if args.set_stt_endpoint:
        config.voice.stt_endpoint = args.set_stt_endpoint
        changed = True
    if args.set_tts_streaming:
        config.voice.tts_streaming = args.set_tts_streaming == "on"
        changed = True
    if args.set_vad is not None:
        config.voice.vad_aggressiveness = max(0, min(3, args.set_vad))
        changed = True
    if args.set_web_enabled:
        config.web.enabled = args.set_web_enabled == "on"
        changed = True
    if args.set_web_host:
        config.web.host = args.set_web_host
        changed = True
    if args.set_web_port is not None:
        config.web.port = args.set_web_port
        changed = True
    ha = config.home_assistant
    ha_changed = False
    if args.set_ha_enabled:
        ha.enabled = args.set_ha_enabled == "on"
        ha_changed = True
    if args.set_ha_url is not None:
        apply_home_assistant_url(ha, args.set_ha_url)
        ha_changed = True
    if args.set_ha_host is not None:
        ha.host = args.set_ha_host.strip()
        ha_changed = True
    if args.set_ha_port is not None:
        ha.port = max(1, min(65535, args.set_ha_port))
        ha_changed = True
    if args.set_ha_https:
        ha.use_https = args.set_ha_https == "on"
        ha_changed = True
    if args.set_ha_token is not None:
        ha.token = args.set_ha_token or None
        ha_changed = True
    if args.set_ha_timeout is not None:
        ha.timeout = max(1.0, min(300.0, args.set_ha_timeout))
        ha_changed = True
    if args.set_ha_verify_ssl:
        ha.verify_ssl = args.set_ha_verify_ssl == "on"
        ha_changed = True
    if ha_changed:
        config.set_home_assistant(ha)
        changed = True
    if changed:
        save_config(config)
        write("Saved.")
    if args.show or not changed:
        write(f"endpoint: {config.llm_endpoint}")
        write(f"model:    {config.llm_model}")
        write(f"timeout:  {config.llm_timeout}")
        write(f"name:       {config.personality.name}")
        write(f"humour:     {config.personality.humour}")
        write(f"honesty:    {config.personality.honesty}")
        write(f"directness: {config.personality.directness}")
        write(f"stt engine:   {config.voice.stt_engine}")
        write(f"stt model:    {config.voice.stt_model}")
        write(f"stt endpoint: {config.voice.stt_endpoint or '(unset)'}")
        write(f"tts engine:   {config.voice.tts_engine}")
        write(f"tts voice:    {config.voice.tts_voice}")
        write(f"tts endpoint: {config.voice.tts_endpoint or '(unset)'}")
        write(f"tts streaming:{'on' if config.voice.tts_streaming else 'off'}")
        write(f"vad:          {config.voice.vad_aggressiveness}")
        ha = config.home_assistant
        ha_token = "configured" if ha.token else "unset"
        write(
            f"home assistant: {'on' if ha.enabled else 'off'} {ha.url} "
            f"(token {ha_token}, verify ssl {'on' if ha.verify_ssl else 'off'})"
        )
        write(f"web:          {'on' if config.web.enabled else 'off'} {config.web.host}:{config.web.port}")
    return 0


def _parse_plugin_value(text: str) -> object:
    lowered = text.strip().lower()
    if lowered in ("on", "true", "yes"):
        return True
    if lowered in ("off", "false", "no"):
        return False
    try:
        return int(text)
    except ValueError:
        pass
    try:
        return float(text)
    except ValueError:
        return text


def _install_command(target: str | Path, *, python: str = sys.executable) -> list[str]:
    target_path = Path(str(target)).expanduser()
    if target_path.is_dir():
        return [python, "-m", "pip", "install", "-e", str(target_path)]
    return [python, "-m", "pip", "install", str(target)]


def _run_plugins(args: argparse.Namespace, write: Callable[[str], None] = print) -> int:
    from richard.plugins.registry import PluginRegistry

    config = load_config()
    registry = PluginRegistry()
    registry.discover()
    installed = {record.name: record for record in registry.records()}
    if args.plugins_command == "list":
        registry.build(
            config.plugins.enabled, config.plugins.tables,
            persona_name=config.personality.name, data_dir=default_plugins_dir(), write=lambda s: None,
        )
        for record in registry.records():
            detail = f" ({record.error})" if record.error else ""
            write(f"{record.name:<20} {record.version:<10} {record.status}{detail}  [{record.module}]")
        if not registry.records():
            write("No plugins installed.")
        return 0
    if args.plugins_command in ("enable", "disable"):
        if args.plugins_command == "enable" and args.name not in installed:
            write(f"Plugin {args.name} is not installed (richard plugins list).")
            return 1
        enabled = [name for name in config.plugins.enabled if name != args.name]
        if args.plugins_command == "enable":
            enabled.append(args.name)
        config.plugins.enabled = enabled
        save_config(config)
        write(f"{args.name} {args.plugins_command}d. Restart Richard to apply.")
        return 0
    if args.plugins_command == "config":
        table = config.plugins.table(args.name)
        for pair in args.pairs:
            key, separator, value = pair.partition("=")
            if not separator or not key:
                write(f"Expected key=value, got {pair!r}.")
                return 1
            table[key.strip()] = _parse_plugin_value(value)
        save_config(config)
        write(f"{args.name}: " + ", ".join(f"{k}={v}" for k, v in table.items() if k != "token"))
        return 0
    if args.plugins_command == "install":
        command = _install_command(args.target)
        write("$ " + " ".join(command))
        completed = subprocess.run(command)
        if completed.returncode != 0:
            return completed.returncode
        write("Installed. Enable it with: richard plugins enable <name>")
        return 0
    return 2


def _run_memory(args: argparse.Namespace, write: Callable[[str], None] = print) -> int:
    store = MemoryStore(default_memory_path())
    try:
        if args.memory_command == "forget":
            if store.remove(args.id):
                write(f"Forgotten memory {args.id}.")
            else:
                write(f"No memory with id {args.id}.")
            return 0
        memories = store.all()
        if not memories:
            write("No memories yet.")
        for m in memories:
            write(f"[{m.id}] {m.text}")
        return 0
    finally:
        store.close()


def _run_chat() -> int:
    config = load_config()
    brain = _build_brain(config)
    store = MemoryStore(default_memory_path())
    monitor = None
    control_store = None
    registry = None
    try:
        registry = _build_plugins(config)
        control_store, control_reader, control_provider = _build_control_loops(registry)
        providers = _engine_providers(
            memory_provider=MemoryProvider(store),
            plugin_providers=[*registry.providers(), ContextLinesProvider(registry.context_lines())],
            control_provider=control_provider,
            diagnostics=_build_diagnostics(registry),
        )
        engine = Engine(
            brain,
            providers,
            config.personality,
        )
        monitor = _start_control_loop_monitor(
            control_store, control_reader, lambda: engine,
            event_sources=registry.event_sources(),
        )
        run_repl(engine, Conversation())
    finally:
        if registry is not None:
            registry.shutdown()
        monitor_stopped = True
        if monitor is not None:
            monitor_stopped = monitor.stop()
        if control_store is not None and monitor_stopped:
            control_store.close()
    return 0


def _voice_deps_available(stt_engine: str = "local", tts_engine: str = "kokoro") -> str | None:
    # Only require what the selected engines actually import. Remote engines need
    # just httpx (a base dep), so a full-remote setup needs no heavy voice extras.
    needed = ["sounddevice", "webrtcvad"]  # mic capture + endpointing, always local
    if stt_engine != "remote":
        needed.append("faster_whisper")
    if tts_engine == "kokoro":
        needed.append("kokoro_onnx")
    elif tts_engine == "piper":
        needed.append("piper.voice")  # piper-tts ships `piper` as a namespace package
    for module in needed:
        try:
            __import__(module)
        except ImportError:
            return module
    return None


def _serve_deps_available(stt_engine: str = "remote", tts_engine: str = "remote") -> str | None:
    # The relay server has no local mic/speaker (the hub provides those), so sounddevice is
    # NOT needed — unlike `voice`. It does host-side endpointing (webrtcvad) and runs the
    # WebSocket transport (websockets). Remote engines need only httpx (a base dep).
    needed = ["webrtcvad", "websockets"]
    if stt_engine != "remote":
        needed.append("faster_whisper")
    if tts_engine == "kokoro":
        needed.append("kokoro_onnx")
    elif tts_engine == "piper":
        needed.append("piper.voice")
    for module in needed:
        try:
            __import__(module)
        except ImportError:
            return module
    return None


def _build_tts(config, write: Callable[[str], None]):
    """Build the configured TTS engine: remote (GPU server), kokoro, or piper."""
    engine = config.voice.tts_engine
    if engine == "remote":
        from richard.voice.remote import RemoteTTS

        return RemoteTTS(
            config.voice.tts_endpoint, config.voice.tts_voice,
            model=config.voice.tts_model,
            language=config.voice.tts_language or None,
            instructions=config.voice.tts_instructions or None,
            x_vector_only=config.voice.tts_xvec_only,
            task_type=config.voice.tts_task_type or None,
            exaggeration=config.voice.tts_exaggeration,
            cfg_weight=config.voice.tts_cfg_weight,
            temperature=config.voice.tts_temperature,
            speed_factor=config.voice.tts_speed,
        )
    if engine == "piper":
        from richard.voice.tts import PiperTTS, ensure_voice

        return PiperTTS(ensure_voice(config.voice.tts_voice, write=write))
    from richard.voice.tts import KokoroTTS, ensure_kokoro

    model, voices = ensure_kokoro(write=write)
    return KokoroTTS(model, voices, voice=config.voice.tts_voice)


def _build_stt(config):
    """Build the configured STT engine: remote (Whisper server) or local faster-whisper."""
    if config.voice.stt_engine == "remote":
        from richard.voice.remote import RemoteSTT

        return RemoteSTT(config.voice.stt_endpoint)
    from richard.voice.stt import WhisperSTT

    return WhisperSTT(config.voice.stt_model)


def _realtime_session_factory(config, *, brain, providers_fn, synth, transcriber, vad_factory):
    """Build the per-connection session factory for /v1/realtime.

    One transcriber and one TTS engine are shared across sessions (models load
    once); each session gets its own Engine, detector, and VAD state.
    """
    from richard.realtime.session import RealtimeSession
    from richard.realtime.vad import EndpointDetector

    def factory(emit):
        detector = EndpointDetector(
            vad_factory(), silence_ms=config.voice.endpoint_silence_ms
        )
        engine = Engine(brain, providers_fn(), config.personality)
        return RealtimeSession(
            engine=engine, transcriber=transcriber, tts=synth,
            detector=detector, emit=emit,
        )

    return factory


async def _serve_realtime_guarded(serve_coro, write):
    """Realtime is additive: a runtime failure (e.g. port already in use) must not
    take down the relay + web servers running in the same loop."""
    try:
        await serve_coro
    except Exception as exc:
        write(f"Realtime API disabled ({exc})")


class ContextLinesProvider:
    """Carries the enabled plugins' capability lines into the system prompt; no tools."""

    def __init__(self, lines: list[str]) -> None:
        self._lines = list(lines)

    def schemas(self) -> list[dict]:
        return []

    def execute(self, name: str, arguments: dict) -> str:
        return f"Unknown tool: {name}."

    def context(self) -> str | None:
        return "\n".join(self._lines) or None


def _build_plugins(config, write: Callable[[str], None] = print):
    """Discover installed plugins and build the enabled ones. Never raises for a plugin."""
    from richard.config import default_plugins_dir
    from richard.plugins.registry import PluginRegistry

    registry = PluginRegistry()
    registry.discover()
    registry.build(
        config.plugins.enabled, config.plugins.tables,
        persona_name=config.personality.name, data_dir=default_plugins_dir(), write=write,
    )
    return registry


def _build_diagnostics(registry):
    """One diagnostics service per runtime assembly; None when nothing can be read."""
    from richard.diagnostics import DiagnosticsService

    readers = registry.target_readers()
    if not readers:
        return None
    return DiagnosticsProvider(DiagnosticsService(readers=readers))


def _engine_providers(*, memory_provider, plugin_providers, control_provider, diagnostics):
    """The provider list shared by every engine: memory, plugins, loops, diagnostics."""
    providers = [memory_provider, *plugin_providers, control_provider]
    if diagnostics is not None:
        providers.append(diagnostics)
    return providers


def _build_control_loops(registry):
    """Build the shared persistent loop store, target reader, and LLM tools."""
    from richard.control_loops import ControlLoopStore, ControlTargetReader, default_control_loops_path
    from richard.providers.control_loop import ControlLoopProvider

    store = ControlLoopStore(default_control_loops_path())
    reader = ControlTargetReader(readers=registry.target_readers())
    return store, reader, ControlLoopProvider(store, reader)


def _start_control_loop_monitor(
    store, reader, engine_factory, *, initial_delay_seconds: float = 0.0, event_sources=()
):
    from richard.control_loops import ControlLoopMonitor

    def notify(change):
        conversation = Conversation()
        conversation.add_user(change.llm_prompt())
        return engine_factory().respond(conversation)

    monitor = ControlLoopMonitor(
        store, reader, notify, initial_delay_seconds=initial_delay_seconds,
        event_sources=event_sources,
    )
    monitor.start()
    return monitor


def _run_voice(write: Callable[[str], None] = print) -> int:
    config = load_config()
    missing = _voice_deps_available(config.voice.stt_engine, config.voice.tts_engine)
    if missing:
        write(
            f"Voice extras not installed (missing '{missing}'). "
            "Install with: pip install 'richard-companion[voice]'"
        )
        return 1
    if config.voice.tts_engine == "remote" and not config.voice.tts_endpoint:
        write("tts_engine is 'remote' but tts_endpoint is unset — run: richard config --set-tts-endpoint URL")
        return 1
    if config.voice.stt_engine == "remote" and not config.voice.stt_endpoint:
        write("stt_engine is 'remote' but stt_endpoint is unset — run: richard config --set-stt-endpoint URL")
        return 1
    from richard.voice import audio, vad
    from richard.voice.loop import run_voice_loop
    from richard.voice.tts import SpeechPipeline

    brain = _build_brain(config)
    store = MemoryStore(default_memory_path())
    speech = None
    monitor = None
    control_store = None
    registry = None
    try:
        registry = _build_plugins(config, write)
        control_store, control_reader, control_provider = _build_control_loops(registry)
        providers = _engine_providers(
            memory_provider=MemoryProvider(store),
            plugin_providers=[*registry.providers(), ContextLinesProvider(registry.context_lines())],
            control_provider=control_provider,
            diagnostics=_build_diagnostics(registry),
        )
        engine = Engine(
            brain, providers, config.personality
        )
        monitor = _start_control_loop_monitor(
            control_store, control_reader, lambda: engine,
            event_sources=registry.event_sources(),
        )
        stt = _build_stt(config)

        def play_pcm(pcm: bytes, sr: int) -> None:
            audio.play(pcm, sr, device=config.voice.output_device)

        speech = SpeechPipeline(_build_tts(config, write), play_pcm)
        rate = config.voice.samplerate

        def record_utterance() -> bytes:
            frames = audio.record_stream(rate, device=config.voice.input_device)
            try:
                return vad.record_utterance(
                    frames,
                    rate,
                    aggressiveness=config.voice.vad_aggressiveness,
                    silence_ms=config.voice.silence_ms,
                )
            finally:
                frames.close()

        run_voice_loop(
            engine,
            Conversation(),
            stt,
            speech,
            record_utterance,
            samplerate=rate,
            streaming=config.voice.tts_streaming,
            write=write,
        )
    finally:
        if registry is not None:
            registry.shutdown()
        monitor_stopped = True
        try:
            if monitor is not None:
                monitor_stopped = monitor.stop()
            if speech is not None:
                speech.close()
        finally:
            if control_store is not None and monitor_stopped:
                control_store.close()
    return 0


def _run_serve(write: Callable[[str], None] = print) -> int:
    config = load_config()
    missing = _serve_deps_available(config.voice.stt_engine, config.voice.tts_engine)
    if missing:
        write(
            f"Relay server deps not installed (missing '{missing}'). "
            "Install with: pip install 'richard-companion[voice]'"
        )
        return 1
    if config.voice.tts_engine == "remote" and not config.voice.tts_endpoint:
        write("tts_engine is 'remote' but tts_endpoint is unset — run: richard config --set-tts-endpoint URL")
        return 1
    if config.voice.stt_engine == "remote" and not config.voice.stt_endpoint:
        write("stt_engine is 'remote' but stt_endpoint is unset — run: richard config --set-stt-endpoint URL")
        return 1
    from richard.satellite.manager import SatelliteManager
    from richard.satellite.relays import RelayRegistry

    brain = _build_brain(config)
    stt = _build_stt(config)
    synth = _build_tts(config, write)
    memory_store = MemoryStore(default_memory_path())
    memory_provider = MemoryProvider(memory_store)
    relays = RelayRegistry()
    registry = _build_plugins(config, write)

    control_store, control_reader, control_provider = _build_control_loops(registry)
    # One diagnostics service for the whole serve assembly: satellite turns and the
    # chat engines all check through the same live target sources.
    diagnostics_provider = _build_diagnostics(registry)
    plugin_providers = [*registry.providers(), ContextLinesProvider(registry.context_lines())]

    import asyncio
    from richard.satellite.server import serve
    manager = SatelliteManager(
        brain=brain, memory_provider=memory_provider,
        personality=config.personality, stt=stt, synthesizer=synth,
        relays=relays,
        extra_providers=[
            *plugin_providers,
            control_provider,
            *([diagnostics_provider] if diagnostics_provider is not None else []),
        ],
    )
    def _restart() -> None:
        # Re-exec the same `richard serve` in place after the HTTP reply flushes.
        # Listening sockets are close-on-exec, so the ports free for the fresh
        # process; it picks up new config and new code. No supervisor required.
        import os
        import sys

        loop = asyncio.get_running_loop()
        loop.call_later(0.3, lambda: os.execvp(sys.argv[0], sys.argv))

    def _serve_providers():
        return _engine_providers(
            memory_provider=memory_provider,
            plugin_providers=plugin_providers,
            control_provider=control_provider,
            diagnostics=diagnostics_provider,
        )

    web_app = None
    if config.web.enabled:
        from richard.config import default_config_path
        from richard.web import WebApp

        def _chat_engine():
            # Same engine the voice path uses: persona + live device control + memory.
            return Engine(brain, _serve_providers(), config.personality)

        # Browser voice turn: STT (remote only) → engine → TTS. Needs the remote Whisper
        # server (it decodes the browser's webm); skipped for the local STT engine.
        voice_turn = None
        if config.voice.stt_engine == "remote":
            from richard.web.app import _voice_turn

            def voice_turn(messages, audio):
                return _voice_turn(stt.transcribe_file, _chat_engine(), synth, messages, audio)

        web_app = WebApp(
            config_path=default_config_path(),
            memory_store=memory_store,
            control_loop_store=control_store,
            control_target_reader=control_reader,
            relays=relays,
            restart=_restart,
            engine_factory=_chat_engine,
            voice_turn=voice_turn,
        )

    def _control_engine():
        return Engine(brain, _serve_providers(), config.personality)

    monitor = _start_control_loop_monitor(
        control_store,
        control_reader,
        _control_engine,
        # Give device connections and satellite reconnects a moment to settle before
        # comparing against the last snapshot from the previous process.
        initial_delay_seconds=10.0,
        event_sources=registry.event_sources(),
    )

    web_ssl = None
    if web_app is not None and config.web.tls:
        import ssl as _ssl
        from richard.config import default_config_path
        from richard.web.tls import ensure_self_signed_cert

        cert_dir = default_config_path().parent
        try:
            cert, key = ensure_self_signed_cert(cert_dir / "web-cert.pem", cert_dir / "web-key.pem")
            web_ssl = _ssl.SSLContext(_ssl.PROTOCOL_TLS_SERVER)
            web_ssl.load_cert_chain(cert, key)
        except Exception as exc:  # openssl missing / load failure → fall back to HTTP
            write(f"TLS setup failed ({exc}); serving web UI over HTTP (mic capture won't work)")
            web_ssl = None

    realtime_coro = None
    if config.realtime.enabled:
        try:
            from richard.realtime.server import serve_realtime
            from richard.realtime.stt import TurnTranscriber
            from richard.realtime.vad import SileroVAD, ensure_silero

            silero_path = ensure_silero(write=write)
            transcriber = TurnTranscriber(
                config.voice.stt_model,
                language=None if config.voice.language == "auto" else config.voice.language,
            )
            factory = _realtime_session_factory(
                config, brain=brain, providers_fn=_serve_providers, synth=synth,
                transcriber=transcriber, vad_factory=lambda: SileroVAD(silero_path),
            )
            realtime_coro = _serve_realtime_guarded(
                serve_realtime(
                    factory, config.realtime.host, config.realtime.port,
                    token=config.realtime.token, ssl_context=web_ssl,
                ),
                write,
            )
            scheme = "wss" if web_ssl is not None else "ws"
            write(f"Starting Richard realtime API on {scheme}://{config.realtime.host}:{config.realtime.port}/v1/realtime")
        except Exception as exc:
            # Realtime is additive in Phase 1 — a missing model/dep must not take
            # down the relay + web servers that were working before.
            write(f"Realtime API disabled ({exc})")
            realtime_coro = None

    if web_app is not None:
        scheme = "https" if web_ssl is not None else "http"
        local = f" (bind 0.0.0.0 → {scheme}://localhost:{config.web.port})" if config.web.host == "0.0.0.0" else ""
        write(f"Richard web UI listening on {scheme}://{config.web.host}:{config.web.port}{local}")
    write(f"Richard relay server listening on {config.satellite.host}:{config.satellite.port}")

    async def _serve_all():
        relay = serve(
            manager, config.satellite.host, config.satellite.port,
            web_app=web_app, web_host=config.web.host, web_port=config.web.port,
            web_ssl=web_ssl,
        )
        if realtime_coro is not None:
            await asyncio.gather(relay, realtime_coro)
        else:
            await relay

    try:
        asyncio.run(_serve_all())
    finally:
        registry.shutdown()
        if monitor.stop():
            control_store.close()
    return 0


def _run_setup(args: argparse.Namespace, write: Callable[[str], None] = print) -> int:
    interactive = not args.non_interactive

    def _llm():
        choice = configure_llm(
            url=args.llm_url, key=args.llm_key, model=args.llm_model, interactive=interactive
        )
        return choice.endpoint, choice.api_key, choice.model

    def _provision(plan, w):
        clip = str(
            Path(__file__).resolve().parent.parent.parent
            / "assets" / "voice" / "richard-voice.wav"
        )
        provision_voice_services(plan, gpu=args.gpu, reference_clip=clip)

    # Give the just-started services time to load their models before the final report.
    def _check(targets):
        return wait_for_endpoints(targets, attempts=24, delay=5.0)

    return run_setup(
        configure_llm=_llm,
        provision=_provision,
        check=_check,
        gpu=args.gpu,
        override=args.profile,
        write=write,
    )


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    if args.command == "config":
        return _run_config(args)
    if args.command == "setup":
        return _run_setup(args)
    if args.command == "memory":
        return _run_memory(args)
    if args.command == "plugins":
        return _run_plugins(args)
    if args.command == "voice":
        return _run_voice()
    if args.command == "serve":
        return _run_serve()
    return _run_chat()
