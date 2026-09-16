import numpy as np
import pytest

from richard.realtime.turn import SmartTurn


class FakeOrtSession:
    def __init__(self, logits):
        self.logits = logits
        self.calls = []

    def run(self, _outputs, feeds):
        self.calls.append(feeds)
        return [np.array(self.logits, dtype=np.float32)]


class FakeExtractor:
    def __init__(self):
        self.calls = []

    def __call__(self, audio, **kwargs):
        self.calls.append((audio, kwargs))
        return {"input_features": np.zeros((1, 80, 800), dtype=np.float32)}


def test_is_complete_returns_score_from_session():
    session = FakeOrtSession([[0.83]])
    extractor = FakeExtractor()
    turn = SmartTurn("unused.onnx", session=session, extractor=extractor)

    score = turn.is_complete(b"\x00\x01" * 16000)

    assert score == pytest.approx(0.83, abs=1e-6)
    assert "input_features" in session.calls[0]
    assert session.calls[0]["input_features"].shape == (1, 80, 800)


def test_is_complete_truncates_to_last_8_seconds():
    session = FakeOrtSession([[0.5]])
    extractor = FakeExtractor()
    turn = SmartTurn("unused.onnx", session=session, extractor=extractor)

    # 10 seconds of int16 PCM at 16 kHz — longer than the 8 s / 128000-sample window.
    samples = np.arange(160000, dtype=np.int16)
    turn.is_complete(samples.tobytes())

    audio_passed = extractor.calls[0][0]
    expected = samples[-128000:].astype(np.float32) / 32768.0
    assert audio_passed.shape == (128000,)
    np.testing.assert_allclose(audio_passed, expected)
