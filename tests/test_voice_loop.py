from richard.conversation import Conversation
from richard.errors import BrainUnreachable
from richard.voice.loop import run_voice_loop


class FakeEngine:
    def __init__(self, deltas):
        self.deltas = deltas
        self.called = False

    def respond_streaming(self, conversation):
        self.called = True
        for d in self.deltas:
            yield d


class FakeSTT:
    def __init__(self, texts):
        self.texts = list(texts)
        self.rates = []

    def transcribe(self, pcm, samplerate=16000):
        self.rates.append(samplerate)
        return self.texts.pop(0)


class FakeSpeech:
    def __init__(self):
        self.said = []
        self.drained = 0
        self.stopped = False
        self.cleared = 0

    def say(self, s):
        self.said.append(s)

    def drain(self):
        self.drained += 1

    def clear(self):
        self.cleared += 1

    def stop(self):
        self.stopped = True


def test_one_turn_then_exit():
    engine = FakeEngine(["Fan's ", "off."])
    speech = FakeSpeech()
    reads = iter(["", "exit"])
    convo = Conversation()
    run_voice_loop(
        engine, convo, FakeSTT(["turn the fan off"]), speech,
        record_utterance=lambda: b"x",
        read=lambda prompt: next(reads), write=lambda s: None,
    )
    assert speech.said == ["Fan's off."]
    assert speech.drained == 1
    assert convo.history()[-1].content == "Fan's off."


def test_blank_transcript_skips_llm():
    engine = FakeEngine(["unused"])
    speech = FakeSpeech()
    reads = iter(["", "exit"])
    run_voice_loop(
        engine, Conversation(), FakeSTT([""]), speech,
        record_utterance=lambda: b"x",
        read=lambda prompt: next(reads), write=lambda s: None,
    )
    assert engine.called is False
    assert speech.said == []


def test_brain_unreachable_is_handled():
    class BoomEngine:
        def respond_streaming(self, conversation):
            raise BrainUnreachable("down")
            yield  # makes this a generator

    speech = FakeSpeech()
    reads = iter(["", "exit"])
    writes = []
    run_voice_loop(
        BoomEngine(), Conversation(), FakeSTT(["hello"]), speech,
        record_utterance=lambda: b"x",
        read=lambda prompt: next(reads), write=writes.append,
    )
    # The error path uses clear() (not the latching stop()) so the pipeline
    # survives for later turns. (stop() here comes only from the later "exit".)
    assert speech.cleared == 1
    assert any("can't reach my brain" in w for w in writes)


def test_whole_utterance_mode_says_full_reply_once():
    engine = FakeEngine(["Fan's ", "off. ", "Anything else?"])
    speech = FakeSpeech()
    reads = iter(["", "exit"])
    run_voice_loop(
        engine, Conversation(), FakeSTT(["status"]), speech,
        record_utterance=lambda: b"x", streaming=False,
        read=lambda prompt: next(reads), write=lambda s: None,
    )
    assert speech.said == ["Fan's off. Anything else?"]


def test_configured_samplerate_reaches_stt():
    engine = FakeEngine(["ok."])
    stt = FakeSTT(["hello"])
    reads = iter(["", "exit"])
    run_voice_loop(
        engine, Conversation(), stt, FakeSpeech(),
        record_utterance=lambda: b"x", samplerate=48000,
        read=lambda prompt: next(reads), write=lambda s: None,
    )
    assert stt.rates == [48000]


def test_transcribe_error_is_handled():
    class BoomSTT:
        def transcribe(self, pcm):
            raise RuntimeError("stt crashed")

    engine = FakeEngine(["unused"])
    speech = FakeSpeech()
    reads = iter(["", "exit"])
    writes = []
    run_voice_loop(
        engine, Conversation(), BoomSTT(), speech,
        record_utterance=lambda: b"x",
        read=lambda prompt: next(reads), write=writes.append,
    )
    assert engine.called is False  # a failed transcription never wakes the LLM
    assert any("couldn't transcribe" in w for w in writes)
