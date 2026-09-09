from __future__ import annotations

import json
import re
from collections.abc import Iterator
from dataclasses import dataclass

from richard.brain.completion import Completion
from richard.brain.protocol import Brain
from richard.config import Personality
from richard.conversation import Conversation
from richard.persona import build_system_prompt
from richard.providers.base import Provider

# Small local models often narrate an action ("I'll turn it off.") and end the turn
# without calling a tool, forcing the user to say "do it" just to grant another
# completion. When a turn ends tool-free but the reply promised an action, the engine
# nudges itself once instead. NOTHING_TO_RUN lets the model decline a false positive;
# the sentinel is never shown to the user (same idiom as CONTROL_LOOP_NO_TRIGGER).
NOTHING_TO_RUN = "NOTHING_TO_RUN"

NUDGE_PROMPT = (
    "[ACTION CHECK] You announced an action but no tool was called. If the action "
    "still needs doing, call the right tool now. If there is truly nothing to "
    f"execute, reply exactly {NOTHING_TO_RUN}."
)

# First-person commitments and present-progressive device verbs. A false positive
# costs one short extra completion answered by the sentinel; a false negative is
# the status quo (the user repeats themselves), so the net is deliberately modest.
_PROMISE_RE = re.compile(
    r"(?i)\b(?:"
    r"i['’]?ll|i will|let me|i['’]?m going to|i am going to|"
    r"one (?:moment|sec(?:ond)?)|right away|"
    r"turning|switching|setting|dimming|starting|stopping|opening|closing|locking|unlocking"
    r")\b"
)


def _promises_action(text: str) -> bool:
    return bool(_PROMISE_RE.search(text or ""))


def _is_nothing_to_run(text: str) -> bool:
    return (text or "").strip().rstrip(".").strip().upper() == NOTHING_TO_RUN


@dataclass(frozen=True)
class ClientToolCall:
    """A tool call the engine cannot execute: it belongs to the connected client (the
    Reachy app's `camera`, the browser's webcam). The engine records the call in the
    conversation and ends the turn; the client posts the result as a tool message (and,
    for a camera, the image as a user message) and asks for a new response."""

    id: str
    name: str
    arguments: str  # JSON, as served to the client


def _assistant_tool_call_message(completion) -> dict:
    return {
        "role": "assistant",
        "content": completion.content,
        "tool_calls": [
            {
                "id": call.id,
                "type": "function",
                "function": {"name": call.name, "arguments": json.dumps(call.arguments)},
            }
            for call in completion.tool_calls
        ],
    }


class Engine:
    def __init__(
        self,
        brain: Brain,
        providers: list[Provider],
        personality: Personality,
        max_rounds: int = 5,
    ) -> None:
        self._brain = brain
        self._providers = providers
        self._personality = personality
        self._max_rounds = max_rounds

    def _system_prompt(self) -> str:
        sections = [build_system_prompt(self._personality)]
        for provider in self._providers:
            ctx = provider.context()
            if ctx:
                sections.append(ctx)
        return "\n\n".join(sections)

    def _head(self, conversation: Conversation) -> str:
        """System prompt for this conversation, assembled once and then byte-stable."""
        if conversation.pinned_head is None:
            conversation.pinned_head = self._system_prompt()
        return conversation.pinned_head

    def tool_names(self) -> list[str]:
        """Names of the tools Richard's providers own (client tools with these names are dropped)."""
        return [s["function"]["name"] for provider in self._providers for s in provider.schemas()]

    def _client_schemas(self, client_tools: list[dict] | None) -> list[dict]:
        owned = set(self.tool_names())
        return [t for t in (client_tools or []) if t["function"]["name"] not in owned]

    def _execute(self, name: str, arguments: dict) -> str:
        for provider in self._providers:
            if any(s["function"]["name"] == name for s in provider.schemas()):
                try:
                    return provider.execute(name, arguments)
                except Exception as exc:
                    # A provider bug must not kill the turn: handed back as a
                    # tool result, the model can retry or tell the user.
                    return f"Tool {name} failed: {exc}"
        return f"Unknown tool: {name}."

    def _record_tool_round(
        self, conversation: Conversation, working: list[dict], completion: Completion,
        deferred: frozenset[str] = frozenset(),
    ) -> None:
        """Execute the calls and append the round to both the request and the history.

        The round is persisted verbatim (see Message): the next turn must replay the
        exact served prefix or the prompt cache misses and the whole prompt is
        re-prefilled (measured 2026-09-09: ~2 s per turn after every tool call). The
        nudge round is deliberately not persisted; it is rare and a fake user message
        in history would mislead later turns. Calls in `deferred` belong to the client:
        their result arrives later as a tool message posted by the client.
        """
        tool_message = _assistant_tool_call_message(completion)
        working.append(tool_message)
        conversation.add_tool_call(tool_message["content"], tool_message["tool_calls"])
        for call in completion.tool_calls:
            if call.id in deferred:
                continue
            result = self._execute(call.name, call.arguments)
            working.append({"role": "tool", "tool_call_id": call.id, "content": result})
            conversation.add_tool_result(call.id, result)

    def respond(self, conversation: Conversation) -> str:
        working: list[dict] = [{"role": "system", "content": self._head(conversation)}]
        working += [m.to_chat() for m in conversation.history()]
        schemas = [s for provider in self._providers for s in provider.schemas()]
        last_content = ""
        any_tool_call = False
        nudged = False
        for _ in range(self._max_rounds):
            completion = self._brain.complete(working, schemas)
            if completion.content:
                last_content = completion.content
            if not completion.tool_calls:
                content = completion.content or ""
                if nudged and _is_nothing_to_run(content):
                    # The nudge was a false positive; keep the original reply.
                    return next(
                        (m["content"] for m in reversed(working) if m.get("role") == "assistant"),
                        "",
                    )
                if (
                    not any_tool_call
                    and not nudged
                    and schemas
                    and _promises_action(content)
                ):
                    nudged = True
                    working.append({"role": "assistant", "content": content})
                    working.append({"role": "user", "content": NUDGE_PROMPT})
                    continue
                return content or last_content
            any_tool_call = True
            self._record_tool_round(conversation, working, completion)
        return last_content or "Sorry, I got a bit tangled up."

    def respond_streaming(
        self, conversation: Conversation, client_tools: list[dict] | None = None
    ) -> Iterator[str | ClientToolCall]:
        working: list[dict] = [{"role": "system", "content": self._head(conversation)}]
        working += [m.to_chat() for m in conversation.history()]
        client_schemas = self._client_schemas(client_tools)
        client_names = {s["function"]["name"] for s in client_schemas}
        schemas = [s for provider in self._providers for s in provider.schemas()] + client_schemas
        any_tool_call = False
        nudged = False
        hold = False  # buffer the nudge round so a sentinel reply is never spoken
        turn_text = ""
        for _ in range(self._max_rounds):
            spoken = ""
            tool_calls = []
            for event in self._brain.stream(working, schemas):
                if event.delta:
                    spoken += event.delta
                    if not hold:
                        yield event.delta
                if event.done:
                    tool_calls = event.tool_calls
            turn_text += spoken
            if hold:
                hold = False
                if _is_nothing_to_run(spoken):
                    if not tool_calls:
                        return  # false-positive nudge; the sentinel stays silent
                    spoken = ""
                elif spoken:
                    yield spoken
            if not tool_calls:
                if (
                    not any_tool_call
                    and not nudged
                    and schemas
                    and _promises_action(turn_text)
                ):
                    nudged = True
                    hold = True
                    working.append({"role": "assistant", "content": spoken})
                    working.append({"role": "user", "content": NUDGE_PROMPT})
                    continue
                return
            any_tool_call = True
            client_calls = [c for c in tool_calls if c.name in client_names]
            self._record_tool_round(
                conversation, working, Completion(content=spoken or None, tool_calls=tool_calls),
                deferred=frozenset(c.id for c in client_calls),
            )
            if client_calls:
                # The turn ends here: the client owns the next step. The next
                # response.create runs a fresh turn on the appended history.
                for call in client_calls:
                    yield ClientToolCall(id=call.id, name=call.name, arguments=json.dumps(call.arguments))
                return
        # max_rounds exhausted: the generator just stops; the caller (voice loop) is
        # responsible for any fallback. (respond() returns an error string here instead.)
