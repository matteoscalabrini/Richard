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
    content: str


class Conversation:
    def __init__(self, system_prompt: str = DEFAULT_SYSTEM_PROMPT) -> None:
        self._system_prompt = system_prompt
        self._history: list[Message] = []

    def add_user(self, content: str) -> None:
        self._history.append(Message(role="user", content=content))

    def add_assistant(self, content: str) -> None:
        self._history.append(Message(role="assistant", content=content))

    def messages(self) -> list[Message]:
        return [Message(role="system", content=self._system_prompt), *self._history]

    def history(self) -> list[Message]:
        return list(self._history)
