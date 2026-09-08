from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlsplit

import tomli_w


def clamp_dial(value: object) -> int:
    """Coerce a value to int and clamp it to the 0..100 dial range."""
    try:
        n = int(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        n = 0
    return max(0, min(100, n))


@dataclass
class Personality:
    name: str = "Richard"
    humour: int = 70
    honesty: int = 90
    directness: int = 60
    system_prompt: str = ""  # overrides the built-in base character when non-empty


@dataclass
class BrainRole:
    """One named brain endpoint. Empty/None fields inherit: role → conversational → llm_*."""

    endpoint: str = ""
    model: str = ""
    api_key: str | None = None
    timeout: float | None = None
    extra_body: dict | None = None  # request-body knobs merged verbatim (e.g. chat_template_kwargs)


@dataclass
class Voice:
    stt_engine: str = "local"  # "local" (faster-whisper) or "remote" (Whisper server)
    stt_model: str = "base.en"
    stt_endpoint: str = ""  # OpenAI-compatible base URL when stt_engine == "remote"
    tts_engine: str = "kokoro"  # "kokoro" (natural CPU), "piper" (fast CPU), or "remote" (GPU server)
    tts_voice: str = "bm_lewis"  # kokoro voice id / piper model name / remote voice name
    tts_endpoint: str = ""  # OpenAI-compatible base URL when tts_engine == "remote"
    tts_model: str = "chatterbox"  # remote: the "model" field; blank = omit it (vLLM-Omni serves one checkpoint)
    tts_language: str = ""  # remote: "language" field when set (Qwen3-TTS: English, Italian, ... ; blank = server default)
    tts_instructions: str = ""  # remote: "instructions" field when set (Qwen3-TTS style/emotion control)
    tts_xvec_only: bool = False  # remote/Qwen3-TTS Base: x_vector_only_mode (timbre only, native prosody)
    tts_task_type: str = ""  # remote/Qwen3-TTS: Base | CustomVoice | VoiceDesign; blank = server default
    tts_streaming: bool = True  # speak sentence-by-sentence (low latency) vs whole-utterance
    # Chatterbox generation knobs (remote TTS engine only); defaults match the server's.
    tts_exaggeration: float = 0.4  # expressiveness, 0.25–2.0
    tts_cfg_weight: float = 0.5  # CFG weight / pacing, 0.2–1.0
    tts_temperature: float = 0.8  # randomness, 0.05–1.5
    tts_speed: float = 1.0  # speed factor (1.0 = normal), 0.25–4.0
    vad_aggressiveness: int = 2
    silence_ms: int = 800
    samplerate: int = 16000
    input_device: str | None = None
    output_device: str | None = None
    language: str = "auto"  # STT language hint + TTS voice selection; "auto" = detect per utterance
    endpoint_silence_ms: int = 400  # realtime endpointing: trailing silence that ends an utterance


@dataclass
class HomeAssistant:
    enabled: bool = False
    host: str = "homeassistant.local"
    port: int = 8123
    use_https: bool = False
    token: str | None = None
    timeout: float = 10.0
    verify_ssl: bool = True

    @property
    def url(self) -> str:
        scheme = "https" if self.use_https else "http"
        host = self.host.strip()
        if ":" in host and not host.startswith("["):
            host = f"[{host}]"
        return f"{scheme}://{host}:{self.port}"


def apply_home_assistant_url(settings: HomeAssistant, url: str) -> None:
    """Populate host/port/TLS from the former URL-style setting or CLI input."""
    raw = url.strip()
    if "://" not in raw:
        raw = "http://" + raw
    parsed = urlsplit(raw)
    settings.use_https = parsed.scheme.lower() == "https"
    settings.host = parsed.hostname or HomeAssistant.host
    try:
        settings.port = parsed.port or (443 if settings.use_https else HomeAssistant.port)
    except ValueError:
        settings.port = 443 if settings.use_https else HomeAssistant.port


@dataclass
class Satellite:
    enabled: bool = False
    host: str = "0.0.0.0"
    port: int = 8770
    token: str | None = None


@dataclass
class Web:
    enabled: bool = True
    host: str = "0.0.0.0"
    port: int = 8771
    tls: bool = True  # serve over HTTPS (self-signed) so the browser allows mic capture


@dataclass
class Realtime:
    """The /v1/realtime WebSocket voice API (spec 2026-07-14-voice-realtime-core)."""

    enabled: bool = True
    host: str = "0.0.0.0"
    port: int = 8766
    token: str = ""  # required (query ?token= or Bearer header) when set; empty = open, LAN posture like web


@dataclass
class Config:
    llm_endpoint: str = "http://localhost:8080"
    llm_model: str = "local"
    llm_api_key: str | None = None
    llm_timeout: float = 180.0
    llm_extra_body: dict = field(default_factory=dict)  # [llm_extra_body] table
    personality: Personality = field(default_factory=Personality)
    voice: Voice = field(default_factory=Voice)
    home_assistant: HomeAssistant = field(default_factory=HomeAssistant)
    satellite: Satellite = field(default_factory=Satellite)
    web: Web = field(default_factory=Web)
    realtime: Realtime = field(default_factory=Realtime)
    brains: dict[str, BrainRole] = field(default_factory=dict)


def resolve_brain_role(config: Config, role: str) -> BrainRole:
    """A fully-populated BrainRole. Missing fields fall back per-field: the named
    role, then the conversational role, then the top-level llm_* settings — so a
    config with no [brains] tables behaves exactly like today's single brain."""
    chain = [config.brains.get(role)]
    if role != "conversational":
        chain.append(config.brains.get("conversational"))

    def pick(attr: str, default):
        for entry in chain:
            if entry is not None:
                value = getattr(entry, attr)
                if value not in ("", None):
                    return value
        return default

    return BrainRole(
        endpoint=pick("endpoint", config.llm_endpoint),
        model=pick("model", config.llm_model),
        api_key=pick("api_key", config.llm_api_key),
        timeout=pick("timeout", config.llm_timeout),
        extra_body=pick("extra_body", config.llm_extra_body) or None,
    )


def default_config_path() -> Path:
    return Path.home() / ".richard" / "config.toml"


def load_config(path: Path | None = None) -> Config:
    path = path or default_config_path()
    data: dict = {}
    if path.exists():
        with path.open("rb") as f:
            data = tomllib.load(f)
    p = data.get("personality", {})
    personality = Personality(
        name=p.get("name", Personality.name) or Personality.name,
        humour=clamp_dial(p.get("humour", Personality.humour)),
        honesty=clamp_dial(p.get("honesty", Personality.honesty)),
        directness=clamp_dial(p.get("directness", Personality.directness)),
        system_prompt=p.get("system_prompt", "") or "",
    )
    v = data.get("voice", {})
    voice = Voice(
        stt_engine=v.get("stt_engine", Voice.stt_engine) or Voice.stt_engine,
        stt_model=v.get("stt_model", Voice.stt_model) or Voice.stt_model,
        stt_endpoint=v.get("stt_endpoint", Voice.stt_endpoint),
        tts_engine=v.get("tts_engine", Voice.tts_engine) or Voice.tts_engine,
        tts_voice=v.get("tts_voice", Voice.tts_voice) or Voice.tts_voice,
        tts_endpoint=v.get("tts_endpoint", Voice.tts_endpoint),
        tts_model=str(v.get("tts_model", Voice.tts_model)),
        tts_language=str(v.get("tts_language", Voice.tts_language)),
        tts_instructions=str(v.get("tts_instructions", Voice.tts_instructions)),
        tts_xvec_only=bool(v.get("tts_xvec_only", Voice.tts_xvec_only)),
        tts_task_type=str(v.get("tts_task_type", Voice.tts_task_type)),
        tts_streaming=bool(v.get("tts_streaming", Voice.tts_streaming)),
        tts_exaggeration=float(v.get("tts_exaggeration", Voice.tts_exaggeration)),
        tts_cfg_weight=float(v.get("tts_cfg_weight", Voice.tts_cfg_weight)),
        tts_temperature=float(v.get("tts_temperature", Voice.tts_temperature)),
        tts_speed=float(v.get("tts_speed", Voice.tts_speed)),
        vad_aggressiveness=int(v.get("vad_aggressiveness", Voice.vad_aggressiveness)),
        silence_ms=int(v.get("silence_ms", Voice.silence_ms)),
        samplerate=int(v.get("samplerate", Voice.samplerate)),
        input_device=v.get("input_device", Voice.input_device),
        output_device=v.get("output_device", Voice.output_device),
        language=v.get("language", Voice.language) or Voice.language,
        endpoint_silence_ms=int(v.get("endpoint_silence_ms", Voice.endpoint_silence_ms)),
    )
    h = data.get("home_assistant", {})
    home_assistant = HomeAssistant(
        enabled=bool(h.get("enabled", HomeAssistant.enabled)),
        host=h.get("host", HomeAssistant.host) or HomeAssistant.host,
        port=int(h.get("port", HomeAssistant.port)),
        use_https=bool(h.get("use_https", HomeAssistant.use_https)),
        token=h.get("token", HomeAssistant.token),
        timeout=float(h.get("timeout", HomeAssistant.timeout)),
        verify_ssl=bool(h.get("verify_ssl", HomeAssistant.verify_ssl)),
    )
    # Read configs written by the initial URL-based implementation.
    if "host" not in h and h.get("url"):
        apply_home_assistant_url(home_assistant, str(h["url"]))
    s = data.get("satellite", {})
    satellite = Satellite(
        enabled=bool(s.get("enabled", Satellite.enabled)),
        host=s.get("host", Satellite.host) or Satellite.host,
        port=int(s.get("port", Satellite.port)),
        token=s.get("token", Satellite.token),
    )
    w = data.get("web", {})
    web = Web(
        enabled=bool(w.get("enabled", Web.enabled)),
        host=w.get("host", Web.host) or Web.host,
        port=int(w.get("port", Web.port)),
        tls=bool(w.get("tls", Web.tls)),
    )
    r = data.get("realtime", {})
    realtime = Realtime(
        enabled=bool(r.get("enabled", Realtime.enabled)),
        host=r.get("host", Realtime.host) or Realtime.host,
        port=int(r.get("port", Realtime.port)),
        token=r.get("token", Realtime.token),
    )
    brains: dict[str, BrainRole] = {}
    for role_name, table in (data.get("brains") or {}).items():
        if not isinstance(table, dict):
            continue
        brains[str(role_name)] = BrainRole(
            endpoint=str(table.get("endpoint", "")),
            model=str(table.get("model", "")),
            api_key=table.get("api_key"),
            timeout=float(table["timeout"]) if "timeout" in table else None,
            extra_body=dict(table["extra_body"]) if isinstance(table.get("extra_body"), dict) else None,
        )
    config = Config(
        llm_endpoint=data.get("llm_endpoint", Config.llm_endpoint),
        llm_model=data.get("llm_model", Config.llm_model),
        llm_api_key=data.get("llm_api_key", Config.llm_api_key),
        llm_timeout=float(data.get("llm_timeout", Config.llm_timeout)),
        llm_extra_body=dict(data.get("llm_extra_body") or {}),
        personality=personality,
        voice=voice,
        home_assistant=home_assistant,
        satellite=satellite,
        web=web,
        realtime=realtime,
        brains=brains,
    )
    # Environment overrides take precedence over the file.
    config.llm_endpoint = os.environ.get("RICHARD_LLM_ENDPOINT", config.llm_endpoint)
    config.llm_model = os.environ.get("RICHARD_LLM_MODEL", config.llm_model)
    config.llm_api_key = os.environ.get("RICHARD_LLM_API_KEY", config.llm_api_key)
    env_timeout = os.environ.get("RICHARD_LLM_TIMEOUT")
    if env_timeout:
        try:
            config.llm_timeout = float(env_timeout)
        except ValueError:
            pass
    env_ha_url = os.environ.get("RICHARD_HA_URL")
    if env_ha_url:
        apply_home_assistant_url(config.home_assistant, env_ha_url)
    config.home_assistant.host = os.environ.get(
        "RICHARD_HA_HOST", config.home_assistant.host
    )
    env_ha_port = os.environ.get("RICHARD_HA_PORT")
    if env_ha_port:
        try:
            config.home_assistant.port = int(env_ha_port)
        except ValueError:
            pass
    config.home_assistant.token = os.environ.get(
        "RICHARD_HA_TOKEN", config.home_assistant.token
    )
    env_ha_enabled = os.environ.get("RICHARD_HA_ENABLED")
    if env_ha_enabled:
        config.home_assistant.enabled = env_ha_enabled.strip().lower() in {
            "1", "true", "yes", "on"
        }
    env_ha_https = os.environ.get("RICHARD_HA_HTTPS")
    if env_ha_https:
        config.home_assistant.use_https = env_ha_https.strip().lower() in {
            "1", "true", "yes", "on"
        }
    return config


def save_config(config: Config, path: Path | None = None) -> None:
    path = path or default_config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    # Scalars first, then the [personality] table (valid TOML ordering).
    data: dict = {
        "llm_endpoint": config.llm_endpoint,
        "llm_model": config.llm_model,
        "llm_timeout": config.llm_timeout,
    }
    if config.llm_api_key is not None:
        data["llm_api_key"] = config.llm_api_key
    if config.llm_extra_body:
        data["llm_extra_body"] = config.llm_extra_body
    data["personality"] = {
        "name": config.personality.name,
        "humour": config.personality.humour,
        "honesty": config.personality.honesty,
        "directness": config.personality.directness,
    }
    if config.personality.system_prompt:
        data["personality"]["system_prompt"] = config.personality.system_prompt
    voice_table: dict = {
        "stt_engine": config.voice.stt_engine,
        "stt_model": config.voice.stt_model,
        "stt_endpoint": config.voice.stt_endpoint,
        "tts_engine": config.voice.tts_engine,
        "tts_voice": config.voice.tts_voice,
        "tts_endpoint": config.voice.tts_endpoint,
        "tts_model": config.voice.tts_model,
        "tts_language": config.voice.tts_language,
        "tts_instructions": config.voice.tts_instructions,
        "tts_xvec_only": config.voice.tts_xvec_only,
        "tts_task_type": config.voice.tts_task_type,
        "tts_streaming": config.voice.tts_streaming,
        "tts_exaggeration": config.voice.tts_exaggeration,
        "tts_cfg_weight": config.voice.tts_cfg_weight,
        "tts_temperature": config.voice.tts_temperature,
        "tts_speed": config.voice.tts_speed,
        "vad_aggressiveness": config.voice.vad_aggressiveness,
        "silence_ms": config.voice.silence_ms,
        "samplerate": config.voice.samplerate,
        "language": config.voice.language,
        "endpoint_silence_ms": config.voice.endpoint_silence_ms,
    }
    if config.voice.input_device is not None:
        voice_table["input_device"] = config.voice.input_device
    if config.voice.output_device is not None:
        voice_table["output_device"] = config.voice.output_device
    data["voice"] = voice_table
    home_assistant_table: dict = {
        "enabled": config.home_assistant.enabled,
        "host": config.home_assistant.host,
        "port": config.home_assistant.port,
        "use_https": config.home_assistant.use_https,
        "timeout": config.home_assistant.timeout,
        "verify_ssl": config.home_assistant.verify_ssl,
    }
    if config.home_assistant.token is not None:
        home_assistant_table["token"] = config.home_assistant.token
    data["home_assistant"] = home_assistant_table
    satellite_table: dict = {
        "enabled": config.satellite.enabled,
        "host": config.satellite.host,
        "port": config.satellite.port,
    }
    if config.satellite.token is not None:
        satellite_table["token"] = config.satellite.token
    data["satellite"] = satellite_table
    data["web"] = {
        "enabled": config.web.enabled,
        "host": config.web.host,
        "port": config.web.port,
        "tls": config.web.tls,
    }
    data["realtime"] = {
        "enabled": config.realtime.enabled,
        "host": config.realtime.host,
        "port": config.realtime.port,
        "token": config.realtime.token,
    }
    if config.brains:
        brains_table: dict = {}
        for role_name, role in config.brains.items():
            entry: dict = {}
            if role.endpoint:
                entry["endpoint"] = role.endpoint
            if role.model:
                entry["model"] = role.model
            if role.api_key is not None:
                entry["api_key"] = role.api_key
            if role.timeout is not None:
                entry["timeout"] = role.timeout
            if role.extra_body:
                entry["extra_body"] = role.extra_body
            brains_table[role_name] = entry
        data["brains"] = brains_table
    with path.open("wb") as f:
        tomli_w.dump(data, f)
    # The file can contain LLM and Home Assistant bearer tokens.
    path.chmod(0o600)
