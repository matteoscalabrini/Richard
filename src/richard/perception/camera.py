"""The brain's own eyes on the ambient stream: `camera` served from the latest frame,
plus two presence questions. When no source is live the camera schema is withheld,
so a connected Reachy app's client-side `camera` (spec 3a) is used instead."""
from __future__ import annotations

import json

from richard.perception import image
from richard.providers.base import ToolResult

CAMERA_SCHEMA = {
    "type": "function",
    "function": {
        "name": "camera",
        "description": (
            "Take a picture from the live camera to see what is in front of you: who is here, what "
            "someone is holding, the room. Use it to answer a visual question or investigate "
            "something you noticed. Use the image as evidence for your response or next action; "
            "describe the scene only when the user asks for a description. Each call captures "
            "the current moment. Start with detail "
            "'low'; use 'high' only to read text or small objects; use 'region' (left, right, centre, "
            "top, bottom, or x0,y0,x1,y1 in 0..1) to look closely at one part of the view."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "question": {"type": "string", "description": "The visual question or uncertainty this picture should help resolve."},
                "detail": {"type": "string", "enum": ["low", "high"], "description": "low (default) or high."},
                "region": {"type": "string", "description": "Optional part of the view to crop at full resolution."},
            },
            "required": ["question"],
        },
    },
}

WHO_SCHEMA = {"type": "function", "function": {
    "name": "who_is_here", "description": "Who the camera currently sees, and since when.",
    "parameters": {"type": "object", "properties": {}}}}
LAST_SEEN_SCHEMA = {"type": "function", "function": {
    "name": "last_seen", "description": "When a named person was last seen by the camera.",
    "parameters": {"type": "object", "properties": {"name": {"type": "string"}}, "required": ["name"]}}}
ENROL_SCHEMA = {"type": "function", "function": {
    "name": "enrol_face",
    "description": (
        "Learn the face of the one person in view under a name, so you recognise them from now on. "
        "Use it only when that person has told you their name, or someone you trust has introduced "
        "them; never guess a name. Takes three snapshots over a couple of seconds."),
    "parameters": {"type": "object", "properties": {"name": {"type": "string", "description": "The person's name."}},
                   "required": ["name"]}}}
FORGET_SCHEMA = {"type": "function", "function": {
    "name": "forget_face",
    "description": "Stop recognising a person: delete their enrolled face for good (when asked to forget them).",
    "parameters": {"type": "object", "properties": {"name": {"type": "string"}}, "required": ["name"]}}}


class CameraProvider:
    def __init__(self, service) -> None:
        self._service = service

    def schemas(self) -> list[dict]:
        return [CAMERA_SCHEMA] if self._service.live_sources() else []

    def execute(self, name: str, arguments: dict):
        if name != "camera":
            return f"Unknown tool: {name}."
        detail = "high" if arguments.get("detail") == "high" else "low"
        region = (arguments.get("region") or "").strip() or None
        try:
            shot = self._service.snapshot(detail=detail, region=region)
        except ValueError as exc:
            return f"Cannot take that picture: {exc}"
        if shot is None:
            return "No camera is streaming right now, so I cannot look."
        jpeg, meta = shot
        text = json.dumps({"image_attached": True, "image_width": meta["width"], "image_height": meta["height"],
                           "source": meta["source"], "detail": detail, "region": region})
        return ToolResult(text=text, images=(image.data_url(jpeg),))

    def context(self) -> str | None:
        return None


class PresenceProvider:
    def __init__(self, service) -> None:
        self._service = service

    def schemas(self) -> list[dict]:
        return [WHO_SCHEMA, LAST_SEEN_SCHEMA, ENROL_SCHEMA, FORGET_SCHEMA]

    def execute(self, name: str, arguments: dict) -> str:
        if name == "enrol_face":
            who = str(arguments.get("name") or "").strip()
            if not who:
                return "A name is required to enrol someone."
            if not getattr(getattr(self._service, "settings", None), "identity_enabled", False):
                return "Face recognition is off; it has to be enabled in the Perception settings first."
            try:
                samples = self._service.enrol_from_live(who)
            except ValueError as exc:
                return f"Could not enrol {who}: {exc}."
            return f"Enrolled {who} from {samples} snapshots; I will recognise them from now on."
        if name == "forget_face":
            who = str(arguments.get("name") or "").strip()
            if who and self._service.forget_face(who):
                return f"Forgotten: {who} is no longer recognised."
            return f"{who or 'That person'} is not enrolled."
        if name == "who_is_here":
            present = self._service.presence()
            if not present:
                return "Nobody is in view right now."
            return "Present now: " + ", ".join(f"{p['subject']} ({p['source']})" for p in present) + "."
        if name == "last_seen":
            who = str(arguments.get("name") or "").strip()
            row = self._service.log.last_seen(who) if who else None
            if row is None:
                return f"{who or 'That person'} has never been seen by the camera."
            return f"{who}: last event '{row['kind']}' at {row['at']} on {row['source']}."
        return f"Unknown tool: {name}."

    def context(self) -> str | None:
        return None
