"""Event vocabulary for the /v1/realtime WebSocket (OpenAI Realtime API subset).

Pure data, no I/O: builders return dicts (server.py serializes), and
parse_client_event validates inbound JSON, decoding audio payloads to bytes.
Deviation from OpenAI, documented in docs/realtime-api.md: our
transcription.delta carries the full partial transcript (replace semantics),
not an append fragment.
"""
from __future__ import annotations

import base64
import binascii
import json
import uuid

CLIENT_EVENT_TYPES = {
    "session.update",
    "input_audio_buffer.append",
    "response.cancel",
    "conversation.item.create",
}


def new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:12]}"


def session_created(session_id: str, *, output_samplerate: int) -> dict:
    return {
        "type": "session.created",
        "session": {
            "id": session_id,
            "input_audio_samplerate": 16000,
            "output_audio_samplerate": output_samplerate,
        },
    }


def session_updated(session: dict) -> dict:
    return {"type": "session.updated", "session": session}


def speech_started() -> dict:
    return {"type": "input_audio_buffer.speech_started"}


def speech_stopped() -> dict:
    return {"type": "input_audio_buffer.speech_stopped"}


def transcription_delta(item_id: str, text: str) -> dict:
    return {
        "type": "conversation.item.input_audio_transcription.delta",
        "item_id": item_id,
        "delta": text,
    }


def transcription_completed(item_id: str, transcript: str) -> dict:
    return {
        "type": "conversation.item.input_audio_transcription.completed",
        "item_id": item_id,
        "transcript": transcript,
    }


def response_created(response_id: str) -> dict:
    return {"type": "response.created", "response": {"id": response_id}}


def text_delta(response_id: str, delta: str) -> dict:
    return {"type": "response.output_text.delta", "response_id": response_id, "delta": delta}


def audio_delta(response_id: str, pcm: bytes) -> dict:
    return {
        "type": "response.audio.delta",
        "response_id": response_id,
        "delta": base64.b64encode(pcm).decode(),
    }


def response_done(response_id: str, status: str = "completed") -> dict:
    return {"type": "response.done", "response": {"id": response_id, "status": status}}


def item_truncated(item_id: str) -> dict:
    return {"type": "conversation.item.truncated", "item_id": item_id}


def error(message: str, code: str = "invalid_request") -> dict:
    return {"type": "error", "error": {"code": code, "message": message}}


def parse_client_event(raw: str | bytes) -> dict:
    try:
        event = json.loads(raw)
    except (ValueError, TypeError):
        raise ValueError("not valid JSON")
    if not isinstance(event, dict) or not isinstance(event.get("type"), str):
        raise ValueError("event must be a JSON object with a string 'type'")
    etype = event["type"]
    if etype not in CLIENT_EVENT_TYPES:
        raise ValueError(f"unknown event type: {etype}")
    if etype == "input_audio_buffer.append":
        try:
            event["audio"] = base64.b64decode(event.get("audio") or "", validate=True)
        except (binascii.Error, TypeError):
            raise ValueError("input_audio_buffer.append: 'audio' must be base64")
    return event
