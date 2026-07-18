from richard.voice.vad import record_utterance

FRAME = b"\x00" * 640  # 20 ms of 16-bit mono @ 16 kHz


class FakeVad:
    def __init__(self, pattern):
        self.pattern = list(pattern)
        self.i = 0

    def is_speech(self, frame, rate):
        v = self.pattern[self.i] if self.i < len(self.pattern) else False
        self.i += 1
        return v


def test_collects_speech_and_stops_after_trailing_silence():
    frames = [FRAME] * 10
    vad = FakeVad([True, True, True, False, False, True, True])
    out = record_utterance(frames, 16000, frame_ms=20, silence_ms=40, vad=vad)
    assert len(out) == 5 * 640  # 3 speech + 2 trailing silence, then stop


def test_caps_at_max_ms():
    frames = [FRAME] * 100
    vad = FakeVad([True] * 100)
    out = record_utterance(frames, 16000, frame_ms=20, silence_ms=10000, max_ms=60, vad=vad)
    assert len(out) == 3 * 640  # max_frames = 60 / 20


def test_ignores_leading_silence():
    frames = [FRAME] * 10
    vad = FakeVad([False, False, True, False, False])
    out = record_utterance(frames, 16000, frame_ms=20, silence_ms=40, vad=vad)
    assert len(out) == 3 * 640  # 1 speech + 2 trailing silence; leading silence dropped


def test_frame_size_error_propagates():
    import pytest

    class BadFrameVad:
        def is_speech(self, frame, rate):
            raise ValueError("frame must be 10, 20, or 30 ms")

    with pytest.raises(ValueError):
        record_utterance([FRAME], 16000, frame_ms=20, vad=BadFrameVad())
