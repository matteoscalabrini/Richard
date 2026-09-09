from __future__ import annotations

from dataclasses import dataclass

DEFAULT_SYSTEM_PROMPT = (
    "You are Richard, a warm, concise house companion. "
    "You help control the user's smart home and answer questions. "
    "Keep replies short and natural."
)


@dataclass(frozen=True)
class Message:
    role: str
    content: str | None
    # A tool round is stored exactly as it was served (assistant tool_calls, then one
    # tool message per call) so the next turn's prompt starts with the byte-identical
    # sequence the model already saw: the box's prompt cache only matches such a prefix.
    tool_calls: tuple[dict, ...] | None = None
    tool_call_id: str | None = None

    def to_chat(self) -> dict:
        message: dict = {"role": self.role, "content": self.content}
        if self.tool_calls is not None:
            message["tool_calls"] = [dict(call) for call in self.tool_calls]
        if self.tool_call_id is not None:
            message["tool_call_id"] = self.tool_call_id
        return message


class Conversation:
    def __init__(self, system_prompt: str = DEFAULT_SYSTEM_PROMPT) -> None:
        self._system_prompt = system_prompt
        self._history: list[Message] = []
        # The engine pins its assembled system prompt here on the first turn and reuses
        # it for the life of the conversation. Provider context (the memory list above
        # all) changes as tools run; if it changed the head mid-conversation, every
        # turn after a `remember` would re-prefill the whole prompt (2026-09-09: 0 cached
        # tokens on the turn after a memory write). New memories still reach the model
        # through the persisted tool result, and the next conversation gets a fresh head.
        self.pinned_head: str | None = None

    def add_user(self, content: str) -> None:
        self._history.append(Message(role="user", content=content))

    def add_assistant(self, content: str) -> None:
        self._history.append(Message(role="assistant", content=content))

    def add_tool_call(self, content: str | None, tool_calls: list[dict]) -> None:
        self._history.append(
            Message(role="assistant", content=content, tool_calls=tuple(tool_calls))
        )

    def add_tool_result(self, tool_call_id: str, content: str) -> None:
        self._history.append(Message(role="tool", content=content, tool_call_id=tool_call_id))

    def messages(self) -> list[Message]:
        return [Message(role="system", content=self._system_prompt), *self._history]

    def history(self) -> list[Message]:
        return list(self._history)
