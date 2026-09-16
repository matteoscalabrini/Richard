from __future__ import annotations

import json
from dataclasses import dataclass

DEFAULT_SYSTEM_PROMPT = (
    "You are Richard, a warm, concise house companion. "
    "You help control the user's smart home and answer questions. "
    "Keep replies short and natural."
)

PRUNED_IMAGE_STUB = "[earlier picture no longer attached]"


@dataclass(frozen=True)
class Message:
    role: str
    # A string, or a list of OpenAI content parts ({"type": "text", ...} /
    # {"type": "image_url", ...}) served to the brain unchanged. Vision enters here.
    content: str | list[dict] | None
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

    def text(self) -> str:
        """The message's text: the string content, or the joined text parts."""
        if isinstance(self.content, str):
            return self.content
        if isinstance(self.content, list):
            return " ".join(
                p["text"] for p in self.content
                if isinstance(p, dict) and p.get("type") == "text" and isinstance(p.get("text"), str)
            ).strip()
        return ""


def user_parts(text: str | None, image_urls: list[str]) -> list[dict]:
    """Content parts for a user message: the text part first (when given), then one
    image part per data URL. This is the only place the part shapes are spelled out."""
    parts: list[dict] = []
    if text:
        parts.append({"type": "text", "text": text})
    for url in image_urls:
        parts.append({"type": "image_url", "image_url": {"url": url}})
    return parts


class Conversation:
    def __init__(self, system_prompt: str = DEFAULT_SYSTEM_PROMPT) -> None:
        self._system_prompt = system_prompt
        self._history: list[Message] = []
        # A fresh ambient camera frame is working context for one request shape, not
        # durable conversation history. It may be replaced or cleared every turn.
        self._observation: Message | None = None
        # The engine pins its assembled system prompt here on the first turn and reuses
        # it for the life of the conversation. Provider context (the memory list above
        # all) changes as tools run; if it changed the head mid-conversation, every
        # turn after a `remember` would re-prefill the whole prompt (2026-09-09: 0 cached
        # tokens on the turn after a memory write). New memories still reach the model
        # through the persisted tool result, and the next conversation gets a fresh head.
        # Forgetting is the one exception: see pinned_head_revisions below.
        self.pinned_head: str | None = None
        # The provider context revisions the pinned head was built from. A provider bumps
        # its revision only when it revokes context (a forgotten memory), and only then is
        # the head rebuilt — so a deleted fact cannot outlive its row, while remembering
        # still leaves the prefix cache intact.
        self.pinned_head_revisions: tuple[int, ...] = ()

    def add_user(self, content: str | list[dict]) -> None:
        self._history.append(Message(role="user", content=content))

    def prefix_last_user(self, prefix: str) -> bool:
        """Prepend `prefix` (and a newline) to the last message's text, in place, when it
        is a user message that does not already carry it. Used to carry the clock inside
        an already-persisted user message (see richard.clock) instead of a separate,
        ephemeral slot that would desync a replayed tool-round prefix. Returns whether it
        mutated anything."""
        if not self._history or self._history[-1].role != "user":
            return False
        last = self._history[-1]
        if isinstance(last.content, str):
            if last.content.startswith(prefix):
                return False
            self._history[-1] = Message(
                role="user", content=f"{prefix}\n{last.content}",
                tool_calls=last.tool_calls, tool_call_id=last.tool_call_id,
            )
            return True
        if isinstance(last.content, list):
            parts = list(last.content)
            for i, part in enumerate(parts):
                if isinstance(part, dict) and part.get("type") == "text":
                    if isinstance(part.get("text"), str) and part["text"].startswith(prefix):
                        return False
                    parts[i] = {**part, "text": f"{prefix}\n{part.get('text', '')}"}
                    self._history[-1] = Message(role="user", content=parts)
                    return True
            parts.insert(0, {"type": "text", "text": prefix})
            self._history[-1] = Message(role="user", content=parts)
            return True
        return False

    def add_assistant(self, content: str) -> None:
        self._history.append(Message(role="assistant", content=content))

    def add_tool_call(self, content: str | None, tool_calls: list[dict]) -> None:
        self._history.append(
            Message(role="assistant", content=content, tool_calls=tuple(tool_calls))
        )

    def add_tool_result(self, tool_call_id: str, content: str) -> None:
        self._history.append(Message(role="tool", content=content, tool_call_id=tool_call_id))

    @property
    def observation(self) -> Message | None:
        return self._observation

    def set_observation(self, content: list[dict] | None) -> None:
        self._observation = Message(role="user", content=content) if content else None

    def request_history(self) -> list[Message]:
        messages = self.history()
        if self._observation is not None:
            messages.append(self._observation)
        return messages

    def pending_client_calls(self) -> list[str]:
        """Ids of the calls in the most recent tool round that have no tool result yet.

        Server-owned calls get their result appended by the engine in the same turn, so
        anything left pending was handed to the client (a `camera` on the robot or in
        the browser). A plain assistant reply closes the matter: nothing before it is pending.
        """
        answered: set[str] = set()
        for message in reversed(self._history):
            if message.role == "tool" and message.tool_call_id:
                answered.add(message.tool_call_id)
            elif message.role == "assistant" and message.tool_calls:
                return [c["id"] for c in message.tool_calls if c.get("id") not in answered]
            elif message.role == "assistant":
                return []
        return []

    def seal_pending(self, reason: str) -> list[str]:
        """Append an error result for every pending call so the served prefix is well formed
        (a tool call without a result confuses chat templates). Returns the sealed ids."""
        ids = self.pending_client_calls()
        for call_id in ids:
            self.add_tool_result(call_id, json.dumps({"error": reason}))
        return ids

    def prune_images(self, keep: int = 2) -> int:
        """Drop the image parts of all but the last `keep` image-carrying user messages.

        Every picture ever taken used to stay in the replayed history, so a long session
        carried an album the model had to attend to on every turn, with nothing marking
        which frame was current. A pruned message keeps its text and gains a short stub
        so the transcript still reads correctly. Changing an old message changes the
        replayed prefix once, costing one re-prefill on the next turn; it happens only
        when a third image is in history, so at most once per look after the second.
        The caller decides when that cost is affordable.
        """
        indices = [
            i for i, m in enumerate(self._history)
            if m.role == "user" and isinstance(m.content, list)
            and any(isinstance(p, dict) and p.get("type") == "image_url" for p in m.content)
        ]
        removed = 0
        for i in indices[:-keep] if keep > 0 else indices:
            message = self._history[i]
            texts = [p["text"] for p in message.content if isinstance(p, dict) and p.get("type") == "text"]
            removed += sum(1 for p in message.content if isinstance(p, dict) and p.get("type") == "image_url")
            stub = " ".join([*texts, PRUNED_IMAGE_STUB]).strip()
            self._history[i] = Message(role="user", content=[{"type": "text", "text": stub}])
        return removed

    def messages(self) -> list[Message]:
        return [Message(role="system", content=self._system_prompt), *self._history]

    def history(self) -> list[Message]:
        return list(self._history)
