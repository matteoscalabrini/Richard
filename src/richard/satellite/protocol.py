from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field


@dataclass
class Hello:
    relay_id: str
    room_id: str
    room_name: str
    capabilities: list[str] = field(default_factory=list)


@dataclass
class SessionStart:
    pass


@dataclass
class State:
    state: str       # "idle" | "listening" | "thinking" | "speaking"


ControlMessage = Hello | SessionStart | State

_TYPE_TAG = {
    Hello: "hello", SessionStart: "session_start", State: "state",
}
_BY_TAG = {tag: cls for cls, tag in _TYPE_TAG.items()}


def encode(msg: ControlMessage) -> str:
    tag = _TYPE_TAG[type(msg)]
    payload = asdict(msg)
    payload["type"] = tag
    return json.dumps(payload)


def decode(text: str) -> ControlMessage:
    data = json.loads(text)
    tag = data.pop("type", None)
    cls = _BY_TAG.get(tag)
    if cls is None:
        raise ValueError(f"unknown relay message type: {tag!r}")
    return cls(**data)
