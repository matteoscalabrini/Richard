import pytest

from richard.brain.completion import StreamEvent, ToolCall
from richard.config import Personality
from richard.conversation import Conversation
from richard.engine import Engine
from richard.errors import BrainUnreachable


class FakeBrain:
    def __init__(self, scripts):
        self.scripts = list(scripts)
        self.calls = []

    def stream(self, messages, tools=None):
        self.calls.append(list(messages))
        script = self.scripts.pop(0)
        for delta in script.get("deltas", []):
            yield StreamEvent(delta=delta)
        yield StreamEvent(tool_calls=script.get("tool_calls", []), done=True)

    def complete(self, *a, **k):
        raise AssertionError("streaming path must not call complete()")

    def chat(self, *a, **k):
        raise AssertionError("unused")


class FakeProvider:
    def __init__(self):
        self.executed = []

    def schemas(self):
        return [{"type": "function", "function": {"name": "set_fan", "parameters": {}}}]

    def execute(self, name, arguments):
        self.executed.append((name, arguments))
        return "ok"

    def context(self):
        return None


def test_streams_plain_text_reply():
    brain = FakeBrain([{"deltas": ["Fan's ", "off."]}])
    engine = Engine(brain, [FakeProvider()], Personality())
    convo = Conversation()
    convo.add_user("fan off")
    assert "".join(engine.respond_streaming(convo)) == "Fan's off."


def test_executes_tool_then_streams_text():
    brain = FakeBrain([
        {"tool_calls": [ToolCall(id="1", name="set_fan", arguments={"on": False})]},
        {"deltas": ["Done. ", "Fan's off."]},
    ])
    provider = FakeProvider()
    engine = Engine(brain, [provider], Personality())
    convo = Conversation()
    convo.add_user("turn the fan off")
    out = "".join(engine.respond_streaming(convo))
    assert out == "Done. Fan's off."
    assert provider.executed == [("set_fan", {"on": False})]
    assert any(m.get("role") == "tool" and m.get("content") == "ok" for m in brain.calls[1])


def test_honors_max_rounds():
    brain = FakeBrain([
        {"tool_calls": [ToolCall(id=str(i), name="set_fan", arguments={})]} for i in range(10)
    ])
    engine = Engine(brain, [FakeProvider()], Personality(), max_rounds=3)
    convo = Conversation()
    convo.add_user("x")
    list(engine.respond_streaming(convo))
    assert len(brain.calls) == 3


def test_streams_content_alongside_tool_calls():
    brain = FakeBrain([
        {"deltas": ["thinking..."], "tool_calls": [ToolCall(id="1", name="set_fan", arguments={})]},
        {"deltas": ["Done."]},
    ])
    provider = FakeProvider()
    engine = Engine(brain, [provider], Personality())
    convo = Conversation()
    convo.add_user("x")
    out = "".join(engine.respond_streaming(convo))
    assert out == "thinking...Done."
    assistant_msg = next(m for m in brain.calls[1] if m.get("role") == "assistant")
    assert assistant_msg["content"] == "thinking..."
    assert provider.executed == [("set_fan", {})]


def test_propagates_brain_unreachable():
    class DownBrain:
        def stream(self, *a, **k):
            raise BrainUnreachable("down")

        def complete(self, *a, **k):
            raise AssertionError("unused")

        def chat(self, *a, **k):
            raise AssertionError("unused")

    engine = Engine(DownBrain(), [FakeProvider()], Personality())
    convo = Conversation()
    convo.add_user("x")
    with pytest.raises(BrainUnreachable):
        list(engine.respond_streaming(convo))


# --- act-in-same-turn nudge (streaming) ---


def test_streaming_nudges_then_executes_and_streams_result():
    brain = FakeBrain([
        {"deltas": ["I'll turn the fan off."]},
        {"tool_calls": [ToolCall(id="1", name="set_fan", arguments={"on": False})]},
        {"deltas": ["Done — fan's off."]},
    ])
    provider = FakeProvider()
    engine = Engine(brain, [provider], Personality())
    convo = Conversation()
    convo.add_user("turn the fan off")
    out = "".join(engine.respond_streaming(convo))
    assert out == "I'll turn the fan off.Done — fan's off."
    assert provider.executed == [("set_fan", {"on": False})]
    assert any(
        m.get("role") == "user" and "[ACTION CHECK]" in (m.get("content") or "")
        for m in brain.calls[1]
    )


def test_streaming_nudge_sentinel_is_suppressed():
    brain = FakeBrain([
        {"deltas": ["I'll leave that up to you."]},
        {"deltas": ["NOTHING_TO_RUN"]},
    ])
    engine = Engine(brain, [FakeProvider()], Personality())
    convo = Conversation()
    convo.add_user("x")
    assert "".join(engine.respond_streaming(convo)) == "I'll leave that up to you."
    assert len(brain.calls) == 2


def test_streaming_nudge_round_plain_text_is_yielded_whole():
    brain = FakeBrain([
        {"deltas": ["I'll check."]},
        {"deltas": ["Actually the sensor is offline."]},
    ])
    engine = Engine(brain, [FakeProvider()], Personality())
    convo = Conversation()
    convo.add_user("x")
    out = "".join(engine.respond_streaming(convo))
    assert out == "I'll check.Actually the sensor is offline."


def test_streaming_plain_reply_is_not_nudged():
    brain = FakeBrain([{"deltas": ["The fan is off."]}])
    engine = Engine(brain, [FakeProvider()], Personality())
    convo = Conversation()
    convo.add_user("x")
    assert "".join(engine.respond_streaming(convo)) == "The fan is off."
    assert len(brain.calls) == 1


def test_streaming_tool_round_is_persisted_for_the_next_turn():
    brain = FakeBrain([
        {"tool_calls": [ToolCall(id="1", name="set_fan", arguments={"on": True})]},
        {"deltas": ["Fan's on."]},
        {"deltas": ["Sure."]},
    ])
    engine = Engine(brain, [FakeProvider()], Personality())
    convo = Conversation()
    convo.add_user("fan on")
    convo.add_assistant("".join(engine.respond_streaming(convo)))
    convo.add_user("thanks")
    list(engine.respond_streaming(convo))
    served_round = brain.calls[1]
    assert [m["role"] for m in served_round] == ["system", "user", "assistant", "tool"]
    next_turn = brain.calls[2]
    assert next_turn[: len(served_round)] == served_round
    assert next_turn[len(served_round):] == [
        {"role": "assistant", "content": "Fan's on."},
        {"role": "user", "content": "thanks"},
    ]
