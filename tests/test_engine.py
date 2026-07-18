import pytest

from richard.brain.completion import Completion, ToolCall
from richard.config import Personality
from richard.conversation import Conversation
from richard.engine import Engine, _assistant_tool_call_message
from richard.errors import BrainUnreachable
from richard.memory import MemoryStore
from richard.providers.memory import MemoryProvider


class FakeBrain:
    def __init__(self, completions):
        self._completions = list(completions)
        self.calls = []

    def complete(self, messages, tools=None):
        self.calls.append((messages, tools))
        return self._completions.pop(0)

    def chat(self, messages):
        return ""


def _engine(brain, store=None, max_rounds=5):
    store = store or MemoryStore(":memory:")
    return Engine(brain, [MemoryProvider(store)], Personality(), max_rounds=max_rounds), store


def test_respond_returns_plain_reply():
    engine, _ = _engine(FakeBrain([Completion(content="hello", tool_calls=[])]))
    assert engine.respond(Conversation()) == "hello"


def test_respond_executes_tool_then_returns_final():
    brain = FakeBrain(
        [
            Completion(content=None, tool_calls=[ToolCall(id="1", name="remember", arguments={"text": "likes tea"})]),
            Completion(content="Noted.", tool_calls=[]),
        ]
    )
    engine, store = _engine(brain)
    convo = Conversation()
    convo.add_user("I like tea")
    assert engine.respond(convo) == "Noted."
    assert [m.text for m in store.all()] == ["likes tea"]


def test_respond_passes_system_prompt_and_merged_schemas():
    brain = FakeBrain([Completion(content="ok", tool_calls=[])])
    engine, _ = _engine(brain)
    engine.respond(Conversation())
    messages, tools = brain.calls[0]
    assert messages[0]["role"] == "system"
    assert "You are Richard" in messages[0]["content"]
    assert "What you remember about the user:" in messages[0]["content"]
    assert [t["function"]["name"] for t in tools] == ["remember", "forget"]


def test_respond_routes_to_owning_provider():
    class FakeProvider:
        def __init__(self):
            self.executed = []

        def schemas(self):
            return [{"type": "function", "function": {"name": "do_thing", "parameters": {}}}]

        def execute(self, name, arguments):
            self.executed.append((name, arguments))
            return "did it"

        def context(self):
            return "Extra context."

    fp = FakeProvider()
    brain = FakeBrain(
        [
            Completion(content=None, tool_calls=[ToolCall(id="1", name="do_thing", arguments={"x": 1})]),
            Completion(content="done", tool_calls=[]),
        ]
    )
    engine = Engine(brain, [fp], Personality())
    assert engine.respond(Conversation()) == "done"
    assert fp.executed == [("do_thing", {"x": 1})]
    assert "Extra context." in brain.calls[0][0][0]["content"]


def test_respond_bounded_by_max_rounds():
    always = Completion(content=None, tool_calls=[ToolCall(id="1", name="remember", arguments={"text": "x"})])
    brain = FakeBrain([always] * 10)
    engine, _ = _engine(brain, max_rounds=3)
    assert engine.respond(Conversation()) == "Sorry, I got a bit tangled up."
    assert len(brain.calls) == 3


def test_respond_propagates_brain_unreachable():
    class DownBrain:
        def complete(self, messages, tools=None):
            raise BrainUnreachable("down")

        def chat(self, messages):
            return ""

    engine = Engine(DownBrain(), [MemoryProvider(MemoryStore(":memory:"))], Personality())
    with pytest.raises(BrainUnreachable):
        engine.respond(Conversation())


def test_assistant_tool_call_message_passes_content_through():
    completion = Completion(content=None, tool_calls=[ToolCall(id="1", name="remember", arguments={"text": "x"})])
    msg = _assistant_tool_call_message(completion)
    assert msg["content"] is None
    assert msg["tool_calls"][0]["function"]["arguments"] == '{"text": "x"}'


def test_respond_handles_content_alongside_tool_calls():
    brain = FakeBrain(
        [
            Completion(content="thinking...", tool_calls=[ToolCall(id="1", name="remember", arguments={"text": "likes tea"})]),
            Completion(content="Done.", tool_calls=[]),
        ]
    )
    engine, store = _engine(brain)
    assert engine.respond(Conversation()) == "Done."
    assert [m.text for m in store.all()] == ["likes tea"]


# --- act-in-same-turn nudge ---


def test_promises_action_heuristic():
    from richard.engine import _promises_action

    assert _promises_action("I'll turn the fan off.")
    assert _promises_action("Turning it off now.")
    assert _promises_action("Let me dim the lights.")
    assert not _promises_action("The fan is off.")
    assert not _promises_action("Done — lamp's off, confirmed at 40%.")


def test_respond_nudges_promise_without_tool_call_and_executes():
    brain = FakeBrain(
        [
            Completion(content="I'll remember that.", tool_calls=[]),
            Completion(content=None, tool_calls=[ToolCall(id="1", name="remember", arguments={"text": "likes tea"})]),
            Completion(content="Noted.", tool_calls=[]),
        ]
    )
    engine, store = _engine(brain)
    assert engine.respond(Conversation()) == "Noted."
    assert [m.text for m in store.all()] == ["likes tea"]
    nudge_messages, _ = brain.calls[1]
    assert any(
        m.get("role") == "user" and "[ACTION CHECK]" in (m.get("content") or "")
        for m in nudge_messages
    )
    assert any(
        m.get("role") == "assistant" and m.get("content") == "I'll remember that."
        for m in nudge_messages
    )


def test_respond_nudge_sentinel_keeps_original_reply():
    brain = FakeBrain(
        [
            Completion(content="I'll leave that decision to you.", tool_calls=[]),
            Completion(content="NOTHING_TO_RUN", tool_calls=[]),
        ]
    )
    engine, store = _engine(brain)
    assert engine.respond(Conversation()) == "I'll leave that decision to you."
    assert store.all() == []


def test_respond_plain_answer_is_not_nudged():
    brain = FakeBrain([Completion(content="The lamp is off.", tool_calls=[])])
    engine, _ = _engine(brain)
    assert engine.respond(Conversation()) == "The lamp is off."
    assert len(brain.calls) == 1


def test_respond_does_not_nudge_after_a_real_tool_call():
    brain = FakeBrain(
        [
            Completion(content=None, tool_calls=[ToolCall(id="1", name="remember", arguments={"text": "x"})]),
            Completion(content="Done — I'll keep an eye on it.", tool_calls=[]),
        ]
    )
    engine, _ = _engine(brain)
    assert engine.respond(Conversation()) == "Done — I'll keep an eye on it."
    assert len(brain.calls) == 2


def test_respond_nudges_at_most_once():
    brain = FakeBrain(
        [
            Completion(content="I'll do it.", tool_calls=[]),
            Completion(content="I'll do it right away.", tool_calls=[]),
        ]
    )
    engine, _ = _engine(brain)
    assert engine.respond(Conversation()) == "I'll do it right away."
    assert len(brain.calls) == 2


def test_respond_does_not_nudge_without_tools():
    brain = FakeBrain([Completion(content="I'll get to it.", tool_calls=[])])
    engine = Engine(brain, [], Personality())
    assert engine.respond(Conversation()) == "I'll get to it."
    assert len(brain.calls) == 1
