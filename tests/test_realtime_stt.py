import numpy as np

from richard.realtime.stt import TurnTranscriber


class FakeSegment:
    def __init__(self, text):
        self.text = text


class FakeModel:
    def __init__(self):
        self.calls = []

    def transcribe(self, audio, **kwargs):
        self.calls.append((audio, kwargs))
        return iter([FakeSegment(" hello"), FakeSegment(" there")]), None


def _pcm(n=16000):
    return np.zeros(n, dtype=np.int16).tobytes()


def test_final_joins_segments_and_strips():
    t = TurnTranscriber(_model=FakeModel())
    assert t.final(_pcm()) == "hello there"


def test_decode_converts_pcm16_to_float32():
    fake = FakeModel()
    t = TurnTranscriber(_model=fake)
    t.final(np.full(16000, 16384, dtype=np.int16).tobytes())
    audio, _ = fake.calls[0]
    assert audio.dtype == np.float32
    assert abs(float(audio[0]) - 0.5) < 0.001


def test_language_hint_passed_through_and_greedy_decode():
    fake = FakeModel()
    t = TurnTranscriber(language="it", _model=fake)
    t.partial(_pcm())
    _, kwargs = fake.calls[0]
    assert kwargs["language"] == "it"
    assert kwargs["beam_size"] == 1
    assert kwargs["condition_on_previous_text"] is False


def test_empty_pcm_returns_empty_without_model_call():
    fake = FakeModel()
    t = TurnTranscriber(_model=fake)
    assert t.final(b"") == ""
    assert fake.calls == []
