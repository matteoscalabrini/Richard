import numpy as np
from types import SimpleNamespace

from richard.realtime.stt import TurnTranscriber


class FakeSegment:
    def __init__(self, text, no_speech_prob=0.0, avg_logprob=0.0):
        self.text = text
        self.no_speech_prob = no_speech_prob
        self.avg_logprob = avg_logprob


class FakeModel:
    def __init__(self, segments=None):
        self.calls = []
        self._segments = segments if segments is not None else [FakeSegment(" hello"), FakeSegment(" there")]

    def transcribe(self, audio, **kwargs):
        self.calls.append((audio, kwargs))
        return iter(self._segments), None


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


def test_final_language_belongs_to_its_decode_and_legacy_text_stays_compatible():
    class Languages(FakeModel):
        def transcribe(self, audio, **kwargs):
            segments, _ = super().transcribe(audio, **kwargs)
            return segments, SimpleNamespace(language="it" if len(self.calls) == 1 else "en")
    t = TurnTranscriber(_model=Languages())
    first = t.final_with_language(_pcm())
    second = t.final_with_language(_pcm())
    assert (first.text, first.language) == ("hello there", "it")
    assert second.language == "en"
    assert t.final(_pcm()) == "hello there"


def test_auto_language_is_restricted_to_allowed_set():
    model = FakeModel([FakeSegment("ciao")])
    model.detect_language = lambda audio: ("tr", 0.51, [("tr", 0.51), ("it", 0.30), ("en", 0.10)])
    t = TurnTranscriber("base", language=None, languages=("it", "en"), _model=model)
    out = t.final_with_language(b"\x00" * 3200)
    assert out.language == "it"
    assert model.calls[-1][1]["language"] == "it"


def test_hallucinated_segments_are_dropped():
    # Only no_speech_prob > 0.6 drops a segment now — a low avg_logprob alone
    # (e.g. a quiet or mumbled but real utterance) is kept, just logged.
    segs = [
        FakeSegment("İzlediğiniz için teşekkür ederim.", no_speech_prob=0.9, avg_logprob=-0.4),
        FakeSegment("come va", no_speech_prob=0.1, avg_logprob=-0.3),
        FakeSegment("garbage", no_speech_prob=0.1, avg_logprob=-1.5),
    ]
    model = FakeModel(segs)
    t = TurnTranscriber("base", language="it", _model=model)
    assert t.final(b"\x00" * 3200) == "come vagarbage"


def test_partial_does_not_call_detect_language():
    detect_calls = []
    model = FakeModel([FakeSegment("ciao")])
    model.detect_language = lambda audio: detect_calls.append(1) or ("it", 0.9, [("it", 0.9)])
    t = TurnTranscriber("base", language=None, languages=("it", "en"), _model=model)
    t.partial(b"\x00" * 3200)  # no language hint, no prior final — must not detect
    assert detect_calls == []
    assert model.calls[-1][1]["language"] == "it"  # falls back to languages[0]


def test_final_detects_once_and_partial_reuses_it():
    calls = []

    def _detect(audio):
        calls.append(1)
        return ("en", 0.9, [("en", 0.9), ("it", 0.1)])

    model = FakeModel([FakeSegment("hi")])
    model.detect_language = _detect
    t = TurnTranscriber("base", language=None, languages=("it", "en"), _model=model)
    t.final(b"\x00" * 3200)
    assert len(calls) == 1
    assert model.calls[-1][1]["language"] == "en"

    t.partial(b"\x00" * 3200)
    assert len(calls) == 1  # partial reused self._last_language, no new detect call
    assert model.calls[-1][1]["language"] == "en"


def test_detect_language_failure_falls_back_to_first_allowed():
    model = FakeModel([FakeSegment("ciao")])

    def _boom(audio):
        raise RuntimeError("no detector")

    model.detect_language = _boom
    t = TurnTranscriber("base", language=None, languages=("it", "en"), _model=model)
    out = t.final_with_language(b"\x00" * 3200)
    assert out.language == "it"
    assert model.calls[-1][1]["language"] == "it"
