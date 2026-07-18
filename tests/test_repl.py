from richard.conversation import Conversation
from richard.errors import BrainUnreachable
from richard.repl import run_repl


class FakeEngine:
    def __init__(self, reply="hi back"):
        self.reply = reply
        self.calls = []

    def respond(self, conversation):
        self.calls.append(conversation)
        return self.reply


def scripted_reader(inputs):
    it = iter(inputs)

    def read(prompt):
        return next(it)

    return read


def test_repl_sends_input_and_writes_reply():
    engine = FakeEngine("hello!")
    outputs = []
    run_repl(engine, Conversation(), read=scripted_reader(["hi", "exit"]), write=outputs.append)
    assert len(engine.calls) == 1
    assert any("hello!" in line for line in outputs)


def test_repl_skips_empty_input():
    engine = FakeEngine()
    run_repl(engine, Conversation(), read=scripted_reader(["", "  ", "exit"]), write=lambda _: None)
    assert engine.calls == []


def test_repl_handles_brain_unreachable():
    class DownEngine:
        def respond(self, conversation):
            raise BrainUnreachable("down")

    outputs = []
    run_repl(DownEngine(), Conversation(), read=scripted_reader(["hi", "exit"]), write=outputs.append)
    assert any("can't reach my brain" in line for line in outputs)
