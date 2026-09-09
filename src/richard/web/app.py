"""Dependency-free async HTTP server hosting Richard's config UI + JSON API.

Runs alongside the satellite relay server (`richard serve`) on its own port. The
lean core has no web framework, so this is a tiny hand-rolled router over
`asyncio.start_server` — enough to serve a single-page config UI and a small JSON
API for config, memories, control loops, notifications, and connected satellites.

The request handler is synchronous (`WebApp.handle`) so it can be unit-tested
without a socket; `serve_web` wires it to asyncio.
"""

from __future__ import annotations

import asyncio
import base64
import io
import json
import threading
import wave
from collections import Counter
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from richard import __version__
from richard.config import (
    Config,
    apply_home_assistant_url,
    clamp_dial,
    load_config,
    save_config,
)
from richard.conversation import Conversation
from richard.control_loops import ControlLoopStore, ControlTargetReader
from richard.errors import BrainUnreachable, HomeAssistantError
from richard.plugins.home_assistant.client import HomeAssistantClient
from richard.memory import MemoryStore
from richard.persona import BASE_CHARACTER
from richard.satellite.relays import RelayRegistry
from richard.web.static import SPA_HTML

_ICON_PATH = Path(__file__).with_name("icon.png")


@dataclass
class Response:
    status: int
    body: bytes
    content_type: str = "application/json"
    headers: dict | None = None

    @classmethod
    def json(cls, payload, status: int = 200) -> "Response":
        return cls(status, json.dumps(payload).encode(), "application/json")

    @classmethod
    def text(cls, payload: str, status: int = 200, content_type: str = "text/plain") -> "Response":
        return cls(status, payload.encode(), content_type)

    @classmethod
    def html(cls, payload: str, status: int = 200) -> "Response":
        # The UI is embedded in the running Python package. Never let a browser or
        # intermediary keep an older SPA after an updater-driven service restart.
        return cls(
            status,
            payload.encode(),
            "text/html; charset=utf-8",
            {"Cache-Control": "no-store"},
        )

    @classmethod
    def not_found(cls) -> "Response":
        return cls.json({"error": "not found"}, 404)

    @classmethod
    def bad_request(cls, message: str) -> "Response":
        return cls.json({"error": message}, 400)

    @classmethod
    def method_not_allowed(cls) -> "Response":
        return cls.json({"error": "method not allowed"}, 405)


def _config_to_dict(config: Config) -> dict:
    return {
        "llm_endpoint": config.llm_endpoint,
        "llm_model": config.llm_model,
        "llm_api_key": config.llm_api_key,
        "llm_timeout": config.llm_timeout,
        "personality": {
            "name": config.personality.name,
            "humour": config.personality.humour,
            "honesty": config.personality.honesty,
            "directness": config.personality.directness,
            "system_prompt": config.personality.system_prompt,
        },
        "voice": {
            "stt_engine": config.voice.stt_engine,
            "stt_model": config.voice.stt_model,
            "stt_endpoint": config.voice.stt_endpoint,
            "tts_engine": config.voice.tts_engine,
            "tts_voice": config.voice.tts_voice,
            "tts_endpoint": config.voice.tts_endpoint,
            "tts_streaming": config.voice.tts_streaming,
            "tts_exaggeration": config.voice.tts_exaggeration,
            "tts_cfg_weight": config.voice.tts_cfg_weight,
            "tts_temperature": config.voice.tts_temperature,
            "tts_speed": config.voice.tts_speed,
            "vad_aggressiveness": config.voice.vad_aggressiveness,
            "silence_ms": config.voice.silence_ms,
            "samplerate": config.voice.samplerate,
            "input_device": config.voice.input_device,
            "output_device": config.voice.output_device,
            "language": config.voice.language,
            "endpoint_silence_ms": config.voice.endpoint_silence_ms,
        },
        "home_assistant": {
            "enabled": config.home_assistant.enabled,
            "url": config.home_assistant.url,
            "host": config.home_assistant.host,
            "port": config.home_assistant.port,
            "use_https": config.home_assistant.use_https,
            # Write-only in the web API: the browser can replace the credential but
            # config reads never echo a long-lived Home Assistant token over the LAN.
            "token": "",
            "token_configured": bool(config.home_assistant.token),
            "timeout": config.home_assistant.timeout,
            "verify_ssl": config.home_assistant.verify_ssl,
        },
        "satellite": {
            "enabled": config.satellite.enabled,
            "host": config.satellite.host,
            "port": config.satellite.port,
        },
        "web": {
            "enabled": config.web.enabled,
            "host": config.web.host,
            "port": config.web.port,
        },
        "realtime": {
            "enabled": config.realtime.enabled,
            "host": config.realtime.host,
            "port": config.realtime.port,
            # The SPA needs the token to open the WS. Same trust boundary as the rest
            # of /api (LAN + TLS, no auth) — unlike the HA token this one only guards
            # the voice socket, so echoing it does not widen exposure.
            "token": config.realtime.token,
        },
    }


def _home_assistant_entity_to_dict(entity) -> dict:
    return {
        "entity_id": entity.entity_id,
        "name": entity.name,
        "domain": entity.domain,
        "state": entity.state,
    }


def _memory_to_dict(m) -> dict:
    return {"id": m.id, "text": m.text, "created_at": m.created_at, "person": m.person}


def _control_loop_to_dict(loop) -> dict:
    return {
        "id": loop.id,
        "name": loop.name,
        "targets": list(loop.targets),
        "trigger_description": loop.trigger_description,
        "interval_seconds": loop.interval_seconds,
        "enabled": loop.enabled,
        "created_at": loop.created_at,
        "updated_at": loop.updated_at,
        "last_checked_at": loop.last_checked_at,
        "last_changed_at": loop.last_changed_at,
        "has_baseline": loop.last_snapshot is not None,
        "last_error": loop.last_error,
        "kind": loop.kind,
        "schedule": loop.schedule,
        "next_run_at": loop.next_run_at,
    }


def _control_notification_to_dict(notification) -> dict:
    return {
        "id": notification.id,
        "loop_id": notification.loop_id,
        "loop_name": notification.loop_name,
        "summary": notification.summary,
        "response": notification.response,
        "created_at": notification.created_at,
        "read": notification.read,
        "error": notification.error,
    }


def _as_int(value, default: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _as_bool(value) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "on"}
    if isinstance(value, (int, float)):
        return bool(value)
    return False


def _clamp_float(value, lo: float, hi: float) -> float:
    return max(lo, min(hi, float(value)))


def _sse(payload: dict) -> str:
    return f"data: {json.dumps(payload)}\n\n"


def _conversation_from_messages(messages) -> Conversation:
    convo = Conversation()
    for m in messages or []:
        role, content = m.get("role"), m.get("content", "")
        if role == "user":
            convo.add_user(content)
        elif role == "assistant":
            convo.add_assistant(content)
    return convo


def _pcm_to_wav(pcm: bytes, samplerate: int) -> bytes:
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(samplerate)
        w.writeframes(pcm)
    return buf.getvalue()


def _voice_turn(transcribe, engine, synth, messages, audio_bytes) -> dict:
    """One voice turn: STT → engine → TTS. Pure + synchronous (run in a worker thread).

    `transcribe(bytes) -> str`, `engine.respond(conversation) -> str`,
    `synth.synth(text) -> pcm16` with a `samplerate` attribute.
    """
    transcript = (transcribe(audio_bytes) or "").strip()
    if not transcript:
        return {"transcript": "", "reply": "", "audio": None}
    convo = _conversation_from_messages(messages)
    convo.add_user(transcript)
    try:
        reply = engine.respond(convo)
    except BrainUnreachable:
        reply = "I can't reach my brain right now."
    wav = _pcm_to_wav(synth.synth(reply), getattr(synth, "samplerate", 24000))
    audio = "data:audio/wav;base64," + base64.b64encode(wav).decode()
    return {"transcript": transcript, "reply": reply, "audio": audio}


def _chat_sse_events(engine, messages) -> Iterator[str]:
    """Drive the engine and emit Server-Sent Events: one per delta, then a done event.

    Pure + synchronous so it's unit-testable; the async route runs it in a worker thread.
    """
    convo = _conversation_from_messages(messages)
    try:
        for delta in engine.respond_streaming(convo):
            if delta:
                yield _sse({"delta": delta})
    except BrainUnreachable:
        yield _sse({"error": "I can't reach my brain right now."})
    yield _sse({"done": True})


def _apply_config_update(config: Config, patch: dict) -> list[str]:
    """Apply a partial update dict to a Config in place. Returns the list of keys set."""
    changed: list[str] = []
    if "llm_endpoint" in patch:
        config.llm_endpoint = str(patch["llm_endpoint"])
        changed.append("llm_endpoint")
    if "llm_model" in patch:
        config.llm_model = str(patch["llm_model"])
        changed.append("llm_model")
    if "llm_api_key" in patch:
        config.llm_api_key = str(patch["llm_api_key"]) or None
        changed.append("llm_api_key")
    if "llm_timeout" in patch:
        config.llm_timeout = float(patch["llm_timeout"])
        changed.append("llm_timeout")
    p = patch.get("personality")
    if isinstance(p, dict):
        if "name" in p:
            config.personality.name = str(p["name"]) or "Richard"
            changed.append("personality.name")
        if "humour" in p:
            config.personality.humour = clamp_dial(p["humour"])
            changed.append("personality.humour")
        if "honesty" in p:
            config.personality.honesty = clamp_dial(p["honesty"])
            changed.append("personality.honesty")
        if "directness" in p:
            config.personality.directness = clamp_dial(p["directness"])
            changed.append("personality.directness")
        if "system_prompt" in p:
            config.personality.system_prompt = str(p["system_prompt"])
            changed.append("personality.system_prompt")
    v = patch.get("voice")
    if isinstance(v, dict):
        if "stt_engine" in v:
            config.voice.stt_engine = str(v["stt_engine"])
            changed.append("voice.stt_engine")
        if "stt_model" in v:
            config.voice.stt_model = str(v["stt_model"])
            changed.append("voice.stt_model")
        if "stt_endpoint" in v:
            config.voice.stt_endpoint = str(v["stt_endpoint"])
            changed.append("voice.stt_endpoint")
        if "tts_engine" in v:
            config.voice.tts_engine = str(v["tts_engine"])
            changed.append("voice.tts_engine")
        if "tts_voice" in v:
            config.voice.tts_voice = str(v["tts_voice"])
            changed.append("voice.tts_voice")
        if "tts_endpoint" in v:
            config.voice.tts_endpoint = str(v["tts_endpoint"])
            changed.append("voice.tts_endpoint")
        if "tts_streaming" in v:
            config.voice.tts_streaming = _as_bool(v["tts_streaming"])
            changed.append("voice.tts_streaming")
        if "tts_exaggeration" in v:
            config.voice.tts_exaggeration = _clamp_float(v["tts_exaggeration"], 0.25, 2.0)
            changed.append("voice.tts_exaggeration")
        if "tts_cfg_weight" in v:
            config.voice.tts_cfg_weight = _clamp_float(v["tts_cfg_weight"], 0.2, 1.0)
            changed.append("voice.tts_cfg_weight")
        if "tts_temperature" in v:
            config.voice.tts_temperature = _clamp_float(v["tts_temperature"], 0.05, 1.5)
            changed.append("voice.tts_temperature")
        if "tts_speed" in v:
            config.voice.tts_speed = _clamp_float(v["tts_speed"], 0.25, 4.0)
            changed.append("voice.tts_speed")
        if "vad_aggressiveness" in v:
            config.voice.vad_aggressiveness = max(0, min(3, _as_int(v["vad_aggressiveness"], 2)))
            changed.append("voice.vad_aggressiveness")
        if "silence_ms" in v:
            config.voice.silence_ms = _as_int(v["silence_ms"], 800)
            changed.append("voice.silence_ms")
        if "samplerate" in v:
            config.voice.samplerate = _as_int(v["samplerate"], 16000)
            changed.append("voice.samplerate")
        if "input_device" in v:
            config.voice.input_device = v["input_device"] or None
            changed.append("voice.input_device")
        if "output_device" in v:
            config.voice.output_device = v["output_device"] or None
            changed.append("voice.output_device")
        if "language" in v:
            config.voice.language = str(v["language"]) or "auto"
            changed.append("voice.language")
        if "endpoint_silence_ms" in v:
            config.voice.endpoint_silence_ms = _as_int(v["endpoint_silence_ms"], 400)
            changed.append("voice.endpoint_silence_ms")
    h = patch.get("home_assistant")
    if isinstance(h, dict):
        if "enabled" in h:
            config.home_assistant.enabled = _as_bool(h["enabled"])
            changed.append("home_assistant.enabled")
        if "url" in h:
            apply_home_assistant_url(config.home_assistant, str(h["url"]))
            changed.append("home_assistant.url")
        if "host" in h:
            config.home_assistant.host = str(h["host"]).strip()
            changed.append("home_assistant.host")
        if "port" in h:
            config.home_assistant.port = max(1, min(65535, _as_int(h["port"], 8123)))
            changed.append("home_assistant.port")
        if "use_https" in h:
            config.home_assistant.use_https = _as_bool(h["use_https"])
            changed.append("home_assistant.use_https")
        if "token" in h:
            config.home_assistant.token = str(h["token"]) or None
            changed.append("home_assistant.token")
        if "timeout" in h:
            config.home_assistant.timeout = _clamp_float(h["timeout"], 1.0, 300.0)
            changed.append("home_assistant.timeout")
        if "verify_ssl" in h:
            config.home_assistant.verify_ssl = _as_bool(h["verify_ssl"])
            changed.append("home_assistant.verify_ssl")
    s = patch.get("satellite")
    if isinstance(s, dict):
        if "enabled" in s:
            config.satellite.enabled = _as_bool(s["enabled"])
            changed.append("satellite.enabled")
        if "host" in s:
            config.satellite.host = str(s["host"])
            changed.append("satellite.host")
        if "port" in s:
            config.satellite.port = _as_int(s["port"], 8770)
            changed.append("satellite.port")
    w = patch.get("web")
    if isinstance(w, dict):
        if "enabled" in w:
            config.web.enabled = _as_bool(w["enabled"])
            changed.append("web.enabled")
        if "host" in w:
            config.web.host = str(w["host"])
            changed.append("web.host")
        if "port" in w:
            config.web.port = _as_int(w["port"], 8771)
            changed.append("web.port")
    rt = patch.get("realtime")
    if isinstance(rt, dict):
        if "enabled" in rt:
            config.realtime.enabled = _as_bool(rt["enabled"])
            changed.append("realtime.enabled")
        if "host" in rt:
            config.realtime.host = str(rt["host"])
            changed.append("realtime.host")
        if "port" in rt:
            config.realtime.port = _as_int(rt["port"], 8766)
            changed.append("realtime.port")
        if "token" in rt:
            config.realtime.token = str(rt["token"])
            changed.append("realtime.token")
    return changed


class WebApp:
    """Routes HTTP requests to the config UI + JSON API. Synchronous handler for testability."""

    def __init__(
        self,
        *,
        config_path,
        memory_store: MemoryStore,
        control_loop_store: ControlLoopStore | None = None,
        control_target_reader: ControlTargetReader | None = None,
        relays: RelayRegistry | None = None,
        load: Callable = load_config,
        save: Callable = save_config,
        restart: Callable[[], None] | None = None,
        engine_factory: Callable[[], object] | None = None,
        voice_turn: Callable[[list, bytes], dict] | None = None,
        home_assistant_client_factory: Callable[..., object] = HomeAssistantClient,
    ) -> None:
        self._config_path = config_path
        self._memory = memory_store
        self._control_loops = control_loop_store
        self._control_targets = control_target_reader
        self._relays = relays
        self._load = load
        self._save = save
        self._restart = restart
        self._engine_factory = engine_factory
        self._voice_turn = voice_turn
        self._home_assistant_client_factory = home_assistant_client_factory

    def handle(self, method: str, path: str, body: bytes = b"") -> Response:
        """Dispatch one request. Catches handler exceptions so a single bad request
        can never crash the server — the async wrapper relies on this."""
        try:
            return self._dispatch(method, path, body)
        except Exception as exc:
            return Response.json({"error": str(exc)}, 500)

    def _dispatch(self, method: str, path: str, body: bytes) -> Response:
        # Static SPA
        if method == "GET" and path in ("/", "/index.html"):
            return Response.html(SPA_HTML)
        if method == "GET" and path == "/icon":
            return self._icon()
        if method == "GET" and path == "/api/status":
            return self._status()
        if method == "GET" and path == "/api/config":
            return self._get_config()
        if method == "PUT" and path == "/api/config":
            return self._put_config(body)
        if method == "GET" and path == "/api/home-assistant":
            return self._home_assistant_status()
        if method == "GET" and path == "/api/memories":
            return self._list_memories()
        if method == "POST" and path == "/api/memories":
            return self._add_memory(body)
        if method == "DELETE" and path.startswith("/api/memories/"):
            return self._delete_memory(path[len("/api/memories/"):])
        if method == "GET" and path == "/api/control-loops":
            return self._list_control_loops()
        if method == "POST" and path == "/api/control-loops":
            return self._add_control_loop(body)
        if method == "PUT" and path.startswith("/api/control-loops/"):
            return self._update_control_loop(path[len("/api/control-loops/"):], body)
        if method == "DELETE" and path.startswith("/api/control-loops/"):
            return self._delete_control_loop(path[len("/api/control-loops/"):])
        if method == "GET" and path == "/api/control-loop-notifications":
            return self._list_control_notifications()
        if method == "DELETE" and path.startswith("/api/control-loop-notifications/"):
            return self._delete_control_notification(
                path[len("/api/control-loop-notifications/"):]
            )
        if method == "POST" and path == "/api/restart":
            return self._restart_serve()
        return Response.not_found()

    # --- API handlers ---

    def _status(self) -> Response:
        satellites = self._relays.ids() if self._relays is not None else []
        loops = self._control_loops.all() if self._control_loops is not None else []
        notifications = (
            self._control_loops.notifications(limit=200)
            if self._control_loops is not None
            else []
        )
        return Response.json(
            {
                "ok": True,
                "version": __version__,
                "satellites": satellites,
                "control_loops": len(loops),
                "unread_notifications": sum(not n.read for n in notifications),
            }
        )

    def _get_config(self) -> Response:
        payload = _config_to_dict(self._load(self._config_path))
        # read-only: lets the UI show the built-in base as the prompt placeholder
        payload["prompt_default"] = BASE_CHARACTER
        return Response.json(payload)

    def _restart_serve(self) -> Response:
        if self._restart is None:
            return Response.json({"error": "restart unavailable"}, 503)
        self._restart()
        return Response.json({"restarting": True})

    def _icon(self) -> Response:
        try:
            return Response(200, _ICON_PATH.read_bytes(), "image/png")
        except OSError:
            return Response.not_found()

    def _put_config(self, body: bytes) -> Response:
        try:
            patch = json.loads(body or b"{}")
        except (ValueError, TypeError) as exc:
            return Response.bad_request(f"invalid JSON: {exc}")
        if not isinstance(patch, dict):
            return Response.bad_request("expected a JSON object")
        config = self._load(self._config_path)
        try:
            changed = _apply_config_update(config, patch)
        except (TypeError, ValueError) as exc:
            # A scalar field got a non-scalar value (e.g. a buggy client sending
            # {"llm_timeout": {...}}). Report it instead of 500-ing, and persist nothing.
            return Response.bad_request(f"invalid config value: {exc}")
        if changed:
            self._save(config, self._config_path)
        return Response.json({"changed": changed, "config": _config_to_dict(config)})

    def _home_assistant_status(self) -> Response:
        config = self._load(self._config_path)
        ha = config.home_assistant
        result = {
            "enabled": ha.enabled,
            "configured": bool(ha.host and ha.token),
            "connected": False,
            "url": ha.url,
            "entity_count": 0,
            "domains": {},
            "entities": [],
            "error": None,
        }
        if not ha.enabled:
            result["error"] = "Home Assistant integration is disabled."
            return Response.json(result)
        if not ha.host or not ha.token:
            result["error"] = "Home Assistant host and long-lived access token are required."
            return Response.json(result)
        try:
            client = self._home_assistant_client_factory(
                ha.url,
                ha.token,
                timeout=ha.timeout,
                verify_ssl=ha.verify_ssl,
            )
            entities = client.list_entities()
        except (HomeAssistantError, ValueError) as exc:
            result["error"] = str(exc)
            return Response.json(result)
        entities.sort(key=lambda entity: (entity.domain, entity.name.lower(), entity.entity_id))
        result.update(
            {
                "connected": True,
                "entity_count": len(entities),
                "domains": dict(sorted(Counter(entity.domain for entity in entities).items())),
                "entities": [_home_assistant_entity_to_dict(entity) for entity in entities],
            }
        )
        return Response.json(result)

    def _list_memories(self) -> Response:
        return Response.json({"memories": [_memory_to_dict(m) for m in self._memory.all()]})

    def _add_memory(self, body: bytes) -> Response:
        try:
            payload = json.loads(body or b"{}")
        except (ValueError, TypeError) as exc:
            return Response.bad_request(f"invalid JSON: {exc}")
        if not isinstance(payload, dict):
            return Response.bad_request("expected a JSON object")
        text = str(payload.get("text", "")).strip()
        if not text:
            return Response.bad_request("'text' is required")
        person = str(payload.get("person", "you")).strip() or "you"
        memory = self._memory.add(text, person=person)
        return Response.json(_memory_to_dict(memory), status=201)

    def _delete_memory(self, raw_id: str) -> Response:
        try:
            memory_id = int(raw_id)
        except ValueError:
            return Response.bad_request(f"invalid memory id: {raw_id!r}")
        if self._memory.remove(memory_id):
            return Response.json({"forgotten": memory_id})
        return Response.json({"error": f"no memory with id {memory_id}"}, status=404)

    def _require_control_loops(self) -> Response | None:
        if self._control_loops is None:
            return Response.json({"error": "control loops unavailable"}, 503)
        return None

    def _list_control_loops(self) -> Response:
        unavailable = self._require_control_loops()
        if unavailable:
            return unavailable
        return Response.json(
            {"control_loops": [_control_loop_to_dict(loop) for loop in self._control_loops.all()]}
        )

    def _parse_object(self, body: bytes) -> tuple[dict | None, Response | None]:
        try:
            payload = json.loads(body or b"{}")
        except (ValueError, TypeError) as exc:
            return None, Response.bad_request(f"invalid JSON: {exc}")
        if not isinstance(payload, dict):
            return None, Response.bad_request("expected a JSON object")
        return payload, None

    def _resolve_control_targets(self, raw_targets) -> tuple[list[str], str | None]:
        if self._control_targets is not None:
            return self._control_targets.resolve(raw_targets)
        if not isinstance(raw_targets, list) or not raw_targets:
            return [], "'targets' must be a non-empty list"
        targets = [str(target).strip() for target in raw_targets]
        if any(not target.startswith("ha:") for target in targets):
            return [], "targets must use ha:<entity_id> identifiers"
        return targets, None

    def _add_control_loop(self, body: bytes) -> Response:
        unavailable = self._require_control_loops()
        if unavailable:
            return unavailable
        payload, error_response = self._parse_object(body)
        if error_response:
            return error_response
        schedule = payload.get("schedule")
        kind = "scheduled" if schedule is not None else "change"
        raw_targets = payload.get("targets")
        if kind == "scheduled" and not raw_targets:
            # Scheduled loops may have zero targets (pure reminders); only resolve
            # when the caller actually supplied some, matching the tool provider.
            targets: list[str] = []
        else:
            targets, error = self._resolve_control_targets(raw_targets)
            if error:
                return Response.bad_request(error)
        try:
            loop = self._control_loops.create(
                name=str(payload.get("name", "")),
                targets=targets,
                trigger_description=str(payload.get("trigger_description", "")),
                interval_seconds=float(payload.get("interval_seconds", 30.0)),
                kind=kind,
                schedule=schedule,
            )
        except (TypeError, ValueError) as exc:
            return Response.bad_request(str(exc))
        return Response.json(_control_loop_to_dict(loop), 201)

    def _update_control_loop(self, raw_id: str, body: bytes) -> Response:
        unavailable = self._require_control_loops()
        if unavailable:
            return unavailable
        try:
            loop_id = int(raw_id)
        except ValueError:
            return Response.bad_request(f"invalid control loop id: {raw_id!r}")
        payload, error_response = self._parse_object(body)
        if error_response:
            return error_response
        targets = None
        if "targets" in payload:
            raw_targets = payload["targets"]
            current = self._control_loops.get(loop_id)
            if current is not None and current.kind == "scheduled" and raw_targets == []:
                targets = []
            else:
                targets, error = self._resolve_control_targets(raw_targets)
                if error:
                    return Response.bad_request(error)
        try:
            loop = self._control_loops.update(
                loop_id,
                name=str(payload["name"]) if "name" in payload else None,
                targets=targets,
                trigger_description=(
                    str(payload["trigger_description"])
                    if "trigger_description" in payload
                    else None
                ),
                interval_seconds=(
                    float(payload["interval_seconds"])
                    if "interval_seconds" in payload
                    else None
                ),
                enabled=_as_bool(payload["enabled"]) if "enabled" in payload else None,
                schedule=payload["schedule"] if "schedule" in payload else None,
            )
        except (TypeError, ValueError) as exc:
            return Response.bad_request(str(exc))
        if loop is None:
            return Response.json({"error": f"no control loop with id {loop_id}"}, 404)
        return Response.json(_control_loop_to_dict(loop))

    def _delete_control_loop(self, raw_id: str) -> Response:
        unavailable = self._require_control_loops()
        if unavailable:
            return unavailable
        try:
            loop_id = int(raw_id)
        except ValueError:
            return Response.bad_request(f"invalid control loop id: {raw_id!r}")
        if self._control_loops.remove(loop_id):
            return Response.json({"deleted": loop_id})
        return Response.json({"error": f"no control loop with id {loop_id}"}, 404)

    def _list_control_notifications(self) -> Response:
        unavailable = self._require_control_loops()
        if unavailable:
            return unavailable
        return Response.json(
            {
                "notifications": [
                    _control_notification_to_dict(notification)
                    for notification in self._control_loops.notifications()
                ]
            }
        )

    def _delete_control_notification(self, raw_id: str) -> Response:
        unavailable = self._require_control_loops()
        if unavailable:
            return unavailable
        try:
            notification_id = int(raw_id)
        except ValueError:
            return Response.bad_request(f"invalid notification id: {raw_id!r}")
        if self._control_loops.remove_notification(notification_id):
            return Response.json({"deleted": notification_id})
        return Response.json({"error": f"no notification with id {notification_id}"}, 404)


# --- HTTP server wiring ---


_STATUS_LINES = {
    200: "OK", 201: "Created", 400: "Bad Request", 404: "Not Found",
    405: "Method Not Allowed", 409: "Conflict", 500: "Internal Server Error",
    503: "Service Unavailable",
}


def _format_response(response: Response, keep_alive: bool) -> bytes:
    status_line = f"HTTP/1.1 {response.status} {_STATUS_LINES.get(response.status, 'OK')}"
    headers = {
        "Content-Type": response.content_type,
        "Content-Length": str(len(response.body)),
        "Connection": "keep-alive" if keep_alive else "close",
    }
    if response.headers:
        headers.update(response.headers)
    head = status_line + "\r\n" + "".join(f"{k}: {v}\r\n" for k, v in headers.items()) + "\r\n"
    return head.encode() + response.body


async def serve_web(app: WebApp, host: str, port: int, ssl_context=None) -> None:
    """Run the web server forever (until the event loop stops it)."""

    async def handle_conn(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        peer = writer.get_extra_info("peername")
        try:
            await _serve_request(app, reader, writer)
        except (ConnectionError, asyncio.IncompleteReadError):
            pass
        except Exception:
            pass
        finally:
            try:
                writer.close()
                await writer.wait_closed()
            except Exception:
                pass

    server = await asyncio.start_server(handle_conn, host, port, ssl=ssl_context)
    scheme = "https" if ssl_context is not None else "http"
    sockets = ", ".join(str(s.getsockname()) for s in server.sockets)
    print(f"Richard web UI listening on {sockets} ({scheme})", flush=True)
    async with server:
        await server.serve_forever()


async def _serve_request(app: WebApp, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
    """Parse one HTTP/1.1 request, dispatch it, and write the response.

    Supports keep-alive across multiple requests on the same connection so a browser
    can fetch the page then the API without reconnecting.
    """
    while True:
        request_line = await reader.readline()
        if not request_line:
            return  # connection closed
        try:
            parts = request_line.decode("iso-8859-1").strip().split()
            method, path, _version = parts[0], parts[1], parts[2]
        except IndexError:
            return  # malformed request line
        path_no_query = path.split("?", 1)[0]
        headers: dict[str, str] = {}
        while True:
            line = await reader.readline()
            if line in (b"\r\n", b"\n", b""):
                break
            try:
                key, _, value = line.decode("iso-8859-1").strip().partition(":")
                headers[key.lower()] = value.strip()
            except ValueError:
                continue
        length = _as_int(headers.get("content-length"), 0)
        body = await reader.readexactly(length) if length > 0 else b""
        # Chat streams Server-Sent Events, which the single-shot handle() can't do.
        if method == "POST" and path_no_query == "/api/chat":
            await _serve_chat(app, body, writer)
            return  # SSE closes the connection
        # Voice runs STT+LLM+TTS off the loop, so it bypasses the sync handle() too.
        if method == "POST" and path_no_query == "/api/voice":
            await _serve_voice(app, body, writer)
            return
        # The HA inventory fetch is blocking network work that can take seconds. Keep
        # it off the shared asyncio loop so a slow or unreachable Home Assistant
        # cannot stall satellites or the web UI.
        if method == "GET" and path_no_query == "/api/home-assistant":
            response = await asyncio.to_thread(app.handle, method, path_no_query, body)
        else:
            response = app.handle(method, path_no_query, body)  # catches exceptions
        keep_alive = headers.get("connection", "").lower() != "close"
        writer.write(_format_response(response, keep_alive))
        await writer.drain()
        if not keep_alive:
            return


async def _serve_chat(app: WebApp, body: bytes, writer) -> None:
    """Stream a chat reply as Server-Sent Events.

    The brain call is synchronous and slow, so it runs in a worker thread and feeds deltas
    back through a queue — the event loop (and the satellite relay sharing it) never blocks.
    """
    if app._engine_factory is None:
        writer.write(_format_response(Response.json({"error": "chat unavailable"}, 503), False))
        await writer.drain()
        return
    try:
        messages = (json.loads(body or b"{}") or {}).get("messages", [])
    except (ValueError, TypeError):
        writer.write(_format_response(Response.bad_request("invalid JSON"), False))
        await writer.drain()
        return

    writer.write(
        b"HTTP/1.1 200 OK\r\nContent-Type: text/event-stream\r\n"
        b"Cache-Control: no-cache\r\nConnection: close\r\n\r\n"
    )
    await writer.drain()

    engine = app._engine_factory()
    loop = asyncio.get_running_loop()
    queue: asyncio.Queue = asyncio.Queue()

    def worker() -> None:
        try:
            for chunk in _chat_sse_events(engine, messages):
                loop.call_soon_threadsafe(queue.put_nowait, chunk)
        except Exception as exc:  # never let a brain error kill the thread silently
            loop.call_soon_threadsafe(queue.put_nowait, _sse({"error": str(exc)}))
        finally:
            loop.call_soon_threadsafe(queue.put_nowait, None)

    threading.Thread(target=worker, daemon=True).start()
    while True:
        chunk = await queue.get()
        if chunk is None:
            break
        writer.write(chunk.encode())
        await writer.drain()


async def _serve_voice(app: WebApp, body: bytes, writer) -> None:
    """One voice turn (STT → engine → TTS), returned as JSON.

    The pipeline is blocking and slow, so it runs in a thread (`to_thread`) — the relay loop
    is never blocked.
    """
    if app._voice_turn is None:
        writer.write(_format_response(Response.json({"error": "voice unavailable"}, 503), False))
        await writer.drain()
        return
    try:
        payload = json.loads(body or b"{}") or {}
        messages = payload.get("messages", [])
        audio = base64.b64decode(payload.get("audio", "") or "")
    except (ValueError, TypeError) as exc:
        writer.write(_format_response(Response.bad_request(f"invalid request: {exc}"), False))
        await writer.drain()
        return
    try:
        result = await asyncio.to_thread(app._voice_turn, messages, audio)
    except Exception as exc:  # STT/TTS/brain failure → report, don't drop the connection
        result = {"transcript": "", "reply": "", "audio": None, "error": str(exc)}
    writer.write(_format_response(Response.json(result), False))
    await writer.drain()
