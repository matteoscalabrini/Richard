from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class ToolCall:
    id: str
    name: str
    arguments: dict


@dataclass
class Completion:
    content: str | None
    tool_calls: list[ToolCall] = field(default_factory=list)


@dataclass
class StreamEvent:
    delta: str = ""
    tool_calls: list[ToolCall] = field(default_factory=list)
    done: bool = False
