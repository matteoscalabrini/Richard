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
        self.tools = []

    def stream(self, messages, tools=None):
        self.calls.append(list(messages))
        self.tools.append(list(tools or []))
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


import json  # noqa: E402

from richard.engine import ClientToolCall  # noqa: E402

CAMERA = {"type": "function", "function": {"name": "camera", "parameters": {
    "type": "object", "properties": {"question": {"type": "string"}}, "required": ["question"]}}}


def test_client_tool_call_ends_the_turn_and_is_yielded():
    brain = FakeBrain([
        {"deltas": ["Let me look. "], "tool_calls": [ToolCall(id="c1", name="camera", arguments={"question": "what"})]},
    ])
    provider = FakeProvider()
    engine = Engine(brain, [provider], Personality())
    convo = Conversation()
    convo.add_user("what am I holding?")
    out = list(engine.respond_streaming(convo, client_tools=[CAMERA]))
    assert out[0] == "Let me look. "
    assert out[1] == ClientToolCall(id="c1", name="camera", arguments=json.dumps({"question": "what"}))
    assert len(out) == 2
    assert provider.executed == []
    # the brain saw both schemas: the provider's and the client's
    assert [t["function"]["name"] for t in brain.tools[0]] == ["set_fan", "camera"]
    # history: user, assistant tool call with the spoken text; nothing else (no tool result yet)
    hist = [m.to_chat() for m in convo.history()]
    assert hist[1]["role"] == "assistant" and hist[1]["content"] == "Let me look. "
    assert hist[1]["tool_calls"][0]["id"] == "c1"
    assert len(hist) == 2
    assert convo.pending_client_calls() == ["c1"]


def test_next_turn_runs_on_the_appended_history():
    brain = FakeBrain([
        {"tool_calls": [ToolCall(id="c1", name="camera", arguments={"question": "what"})]},
        {"deltas": ["A blue mug."]},
    ])
    engine = Engine(brain, [FakeProvider()], Personality())
    convo = Conversation()
    convo.add_user("look")
    list(engine.respond_streaming(convo, client_tools=[CAMERA]))
    convo.add_tool_result("c1", '{"image_attached": true}')
    convo.add_user([{"type": "image_url", "image_url": {"url": "data:image/jpeg;base64,AAAA"}}])
    assert "".join(engine.respond_streaming(convo, client_tools=[CAMERA])) == "A blue mug."
    served = brain.calls[1]
    assert [m["role"] for m in served] == ["system", "user", "assistant", "tool", "user"]
    assert served[3]["tool_call_id"] == "c1"
    assert served[4]["content"][0]["type"] == "image_url"


def test_mixed_round_executes_provider_calls_and_defers_client_calls():
    brain = FakeBrain([
        {"tool_calls": [ToolCall(id="a", name="set_fan", arguments={"on": True}),
                        ToolCall(id="b", name="camera", arguments={"question": "q"})]},
    ])
    provider = FakeProvider()
    engine = Engine(brain, [provider], Personality())
    convo = Conversation()
    convo.add_user("fan on and look")
    out = list(engine.respond_streaming(convo, client_tools=[CAMERA]))
    assert provider.executed == [("set_fan", {"on": True})]
    assert out == [ClientToolCall(id="b", name="camera", arguments=json.dumps({"question": "q"}))]
    assert convo.pending_client_calls() == ["b"]
    roles = [m.role for m in convo.history()]
    assert roles == ["user", "assistant", "tool"]  # set_fan's result recorded, camera's pending


def test_client_tool_colliding_with_a_provider_is_dropped():
    brain = FakeBrain([{"deltas": ["ok"]}])
    engine = Engine(brain, [FakeProvider()], Personality())
    convo = Conversation()
    convo.add_user("hi")
    clash = {"type": "function", "function": {"name": "set_fan", "parameters": {}}}
    list(engine.respond_streaming(convo, client_tools=[clash, CAMERA]))
    assert [t["function"]["name"] for t in brain.tools[0]] == ["set_fan", "camera"]
    assert engine.tool_names() == ["set_fan"]


def test_client_call_counts_as_an_action_so_no_nudge_follows():
    brain = FakeBrain([
        {"deltas": ["I'll take a look."], "tool_calls": [ToolCall(id="c1", name="camera", arguments={"question": "q"})]},
    ])
    engine = Engine(brain, [FakeProvider()], Personality())
    convo = Conversation()
    convo.add_user("look")
    out = list(engine.respond_streaming(convo, client_tools=[CAMERA]))
    assert isinstance(out[-1], ClientToolCall)
    assert len(brain.calls) == 1  # no ACTION CHECK round


def test_without_client_tools_only_strings_are_yielded():
    brain = FakeBrain([{"deltas": ["plain"]}])
    engine = Engine(brain, [FakeProvider()], Personality())
    convo = Conversation()
    convo.add_user("hi")
    assert all(isinstance(x, str) for x in engine.respond_streaming(convo))
