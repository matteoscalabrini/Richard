from __future__ import annotations

from collections.abc import Iterator
from typing import Protocol

from richard.brain.completion import Completion, StreamEvent
from richard.conversation import Message


class Brain(Protocol):
    def complete(self, messages: list[dict], tools: list[dict] | None = None) -> Completion:
        """Return the model's completion (content and/or tool calls) for the given messages."""
        ...

    def stream(
        self, messages: list[dict], tools: list[dict] | None = None
    ) -> Iterator[StreamEvent]:
        """Yield content deltas as they arrive; the final event carries any tool_calls + done=True."""
        ...

    def chat(self, messages: list[Message]) -> str:
        """Return the assistant's plain-text reply (no tools)."""
        ...
