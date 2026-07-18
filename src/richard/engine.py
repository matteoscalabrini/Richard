from __future__ import annotations

import json
import re
from collections.abc import Iterator

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

    def _execute(self, name: str, arguments: dict) -> str:
        for provider in self._providers:
            if any(s["function"]["name"] == name for s in provider.schemas()):
                return provider.execute(name, arguments)
        return f"Unknown tool: {name}."

    def respond(self, conversation: Conversation) -> str:
        working: list[dict] = [{"role": "system", "content": self._system_prompt()}]
        working += [{"role": m.role, "content": m.content} for m in conversation.history()]
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
            working.append(_assistant_tool_call_message(completion))
            for call in completion.tool_calls:
                result = self._execute(call.name, call.arguments)
                working.append({"role": "tool", "tool_call_id": call.id, "content": result})
        return last_content or "Sorry, I got a bit tangled up."

    def respond_streaming(self, conversation: Conversation) -> Iterator[str]:
        working: list[dict] = [{"role": "system", "content": self._system_prompt()}]
        working += [{"role": m.role, "content": m.content} for m in conversation.history()]
        schemas = [s for provider in self._providers for s in provider.schemas()]
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
            working.append(
                _assistant_tool_call_message(
                    Completion(content=spoken or None, tool_calls=tool_calls)
                )
            )
            for call in tool_calls:
                result = self._execute(call.name, call.arguments)
                working.append(
                    {"role": "tool", "tool_call_id": call.id, "content": result}
                )
        # max_rounds exhausted: the generator just stops; the caller (voice loop) is
        # responsible for any fallback. (respond() returns an error string here instead.)
