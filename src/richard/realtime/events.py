"""Event vocabulary for the /v1/realtime WebSocket (OpenAI Realtime API subset).

Pure data, no I/O: builders return dicts (server.py serializes), parse_client_event
validates inbound JSON (decoding audio payloads to bytes), parse_item normalizes
conversation items (user messages with text and image parts, function_call_output),
and tools_to_schemas turns the client's flat tool specs into chat-completions schemas.
Deviation from OpenAI, documented in docs/realtime-api.md: our
transcription.delta carries the full partial transcript (replace semantics),
not an append fragment.
"""
from __future__ import annotations

import base64
import binascii
import json
import uuid

from richard.conversation import user_parts
from richard.vision import check_image_data_url

CLIENT_EVENT_TYPES = {
    "session.update",
    "input_audio_buffer.append",
    "response.cancel",
    "response.create",
    "conversation.item.create",
    "playback.update",
}

ACTIVE_RESPONSE_CODE = "conversation_already_has_active_response"


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


def response_created(response_id: str, *, turn_id: str | None = None,
                     unsolicited: bool = False, language: str | None = None,
                     cue_voice: str | None = None) -> dict:
    response = {"id": response_id}
    if turn_id is not None:
        response.update(turn_id=turn_id, unsolicited=unsolicited)
    if language is not None:
        response["language"] = language
    if cue_voice is not None:
        response["cue_voice"] = cue_voice
    return {"type": "response.created", "response": response}


def response_activity(response_id: str, turn_id: str, phase: str, *, unsolicited: bool) -> dict:
    if phase not in ("thinking", "tool", "vision", "answer", "error"):
        raise ValueError(f"unknown response activity phase: {phase}")
    return {
        "type": "response.activity",
        "response_id": response_id,
        "turn_id": turn_id,
        "phase": phase,
        "unsolicited": unsolicited,
    }


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


def function_call_arguments_done(response_id: str, call_id: str, name: str, arguments: str) -> dict:
    """The brain called a client-owned tool; the client runs it and posts the result."""
    return {
        "type": "response.function_call_arguments.done",
        "event_id": new_id("event"),
        "response_id": response_id,
        "item_id": new_id("item"),
        "output_index": 0,
        "call_id": call_id,
        "name": name,
        "arguments": arguments,
    }


def tools_to_schemas(tools, reserved=()) -> tuple[list[dict], list[str]]:
    """Realtime flat function specs → chat-completions schemas. Names in `reserved`
    (Richard's own providers) are dropped and reported; junk entries are skipped."""
    schemas: list[dict] = []
    dropped: list[str] = []
    for tool in tools or []:
        if not isinstance(tool, dict) or tool.get("type", "function") != "function":
            continue
        name = tool.get("name")
        if not isinstance(name, str) or not name:
            continue
        if name in reserved:
            dropped.append(name)
            continue
        parameters = tool.get("parameters")
        if not isinstance(parameters, dict) or not parameters:
            parameters = {"type": "object", "properties": {}}
        schemas.append({"type": "function", "function": {
            "name": name,
            "description": str(tool.get("description") or ""),
            "parameters": parameters,
        }})
    return schemas, dropped


def parse_item(item: dict) -> dict:
    """Normalize a conversation.item.create item. Raises ValueError with a client-facing message.

    `message` (default): user role only; `input_text` and `input_image` parts; a text-only
    item becomes a string, anything with an image becomes content parts.
    `function_call_output`: `call_id` and a string `output`.
    """
    itype = item.get("type", "message")
    if itype == "message":
        if item.get("role", "user") != "user":
            raise ValueError("conversation.item.create: only user-role messages are accepted")
        parts = item.get("content")
        if not isinstance(parts, list):
            raise ValueError("conversation.item.create: 'content' must be a list of parts")
        texts: list[str] = []
        images: list[str] = []
        for part in parts:
            if not isinstance(part, dict):
                continue
            if part.get("type") == "input_text" and isinstance(part.get("text"), str):
                texts.append(part["text"].strip())
            elif part.get("type") == "input_image":
                check_image_data_url(part.get("image_url"))
                images.append(part["image_url"])
        text = " ".join(t for t in texts if t).strip()
        if not text and not images:
            raise ValueError("conversation.item.create: no usable content")
        return {"kind": "message", "content": text if not images else user_parts(text or None, images)}
    if itype == "function_call_output":
        call_id, output = item.get("call_id"), item.get("output")
        if not isinstance(call_id, str) or not call_id:
            raise ValueError("function_call_output: 'call_id' must be a non-empty string")
        if not isinstance(output, str):
            raise ValueError("function_call_output: 'output' must be a string")
        return {"kind": "function_call_output", "call_id": call_id, "output": output}
    raise ValueError(f"conversation.item.create: unsupported item type: {itype}")


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
    elif etype == "playback.update":
        if not isinstance(event.get("response_id"), str) or not event["response_id"]:
            raise ValueError("playback.update: 'response_id' must be a non-empty string")
        if not isinstance(event.get("playing"), bool):
            raise ValueError("playback.update: 'playing' must be a boolean")
    return event
