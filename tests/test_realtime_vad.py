import numpy as np

from richard.realtime.vad import (
    FRAME_BYTES,
    FRAME_SAMPLES,
    SileroVAD,
    ensure_silero,
    ensure_smart_turn,
)


class FakeOrtSession:
    """Mimics the Silero v5 ONNX signature: (input, state, sr) -> (prob, new_state)."""

    def __init__(self, probs):
        self.probs = list(probs)
        self.states_seen = []

    def run(self, _outputs, feeds):
        self.states_seen.append(feeds["state"].copy())
        prob = self.probs.pop(0)
        return [np.array([[prob]], dtype=np.float32)], feeds["state"] + 1.0


def _frame(fill=1000):
    return np.full(FRAME_SAMPLES, fill, dtype=np.int16).tobytes()


def test_frame_constants_agree():
    assert FRAME_BYTES == FRAME_SAMPLES * 2


def test_is_speech_thresholds_probability():
    vad = SileroVAD("unused.onnx", threshold=0.5, _session=FakeOrtSession([0.9, 0.1]))
    assert vad.is_speech(_frame()) is True
    assert vad.is_speech(_frame()) is False


def test_state_carries_between_frames_and_resets():
    fake = FakeOrtSession([0.9, 0.9, 0.9])
    vad = SileroVAD("unused.onnx", _session=fake)
    vad.is_speech(_frame())
    vad.is_speech(_frame())
    assert fake.states_seen[1].max() > 0  # second call got the carried state
    vad.reset()
    vad.is_speech(_frame())
    assert fake.states_seen[2].max() == 0  # reset zeroed it


def test_ensure_silero_skips_download_when_present(tmp_path):
    target = tmp_path / "silero_vad.onnx"
    target.write_bytes(b"x" * 2048)  # > 1 KiB guard, mirrors voice.tts._present
    assert ensure_silero(models_dir=tmp_path, client=None) == target


def test_ensure_silero_downloads_when_missing(tmp_path):
    class FakeClient:
        def get(self, url):
            class R:
                content = b"o" * 4096

                def raise_for_status(self):
                    pass

            return R()

    path = ensure_silero(models_dir=tmp_path, client=FakeClient(), write=lambda *_: None)
    assert path.read_bytes() == b"o" * 4096


from richard.realtime.vad import EndpointDetector


class ScriptedVAD:
    def __init__(self, verdicts):
        self.verdicts = list(verdicts)
        self.resets = 0

    def is_speech(self, frame, samplerate=16000):
        return self.verdicts.pop(0)

    def reset(self):
        self.resets += 1


def _detector(verdicts, **kw):
    kw.setdefault("silence_ms", 96)   # 3 frames at 32 ms — small numbers for tests
    kw.setdefault("preroll_ms", 64)   # 2 frames
    return EndpointDetector(ScriptedVAD(verdicts), **kw)


def test_speech_start_includes_preroll():
    d = _detector([False, False, True])
    a, b, c = b"a" * 1024, b"b" * 1024, b"c" * 1024
    assert d.feed(a) == []
    assert d.feed(b) == []
    assert d.feed(c) == [("speech_started",)]
    assert d.in_speech
    assert d.collected() == a + b + c


def test_utterance_ends_after_trailing_silence():
    d = _detector([True, True, False, False, False])
    frames = [bytes([i]) * 1024 for i in range(5)]
    events = [e for f in frames for e in d.feed(f)]
    kinds = [e[0] for e in events]
    assert kinds == ["speech_started", "utterance"]
    assert events[1][1] == b"".join(frames)  # trailing silence kept (natural pause)
    assert not d.in_speech


def test_vad_reset_after_utterance():
    vad = ScriptedVAD([True, False, False, False])
    d = EndpointDetector(vad, silence_ms=96, preroll_ms=64)
    for i in range(4):
        d.feed(bytes([i]) * 1024)
    assert vad.resets == 1


def test_max_length_forces_endpoint():
    d = _detector([True] * 5, max_ms=128)  # 4 frames
    events = [e for i in range(5) for e in d.feed(bytes([i]) * 1024)]
    assert [e[0] for e in events] == ["speech_started", "utterance"]


def test_preroll_ring_is_bounded():
    d = _detector([False] * 10 + [True])
    for i in range(10):
        d.feed(bytes([i]) * 1024)
    d.feed(b"s" * 1024)
    # preroll_ms=64 → only the last 2 silent frames precede the speech frame
    assert d.collected() == bytes([8]) * 1024 + bytes([9]) * 1024 + b"s" * 1024


class ScriptedPredictor:
    def __init__(self, scores):
        self.scores = list(scores)
        self.calls = []

    def is_complete(self, pcm):
        self.calls.append(pcm)
        return self.scores.pop(0)


def test_turn_predictor_ends_utterance_on_high_score_before_ceiling():
    # speech, then trailing silence; min_silence_ms=64 (2 frames), predictor polled
    # every 64 ms (2 frames), ceiling at max_silence_ms=320 (10 frames).
    predictor = ScriptedPredictor([0.1, 0.9])
    d = _detector(
        [True, False, False, False, False],
        turn_predictor=predictor,
        min_silence_ms=64,
        max_silence_ms=320,
        predictor_every_ms=64,
    )
    events = [e for i in range(5) for e in d.feed(bytes([i]) * 1024)]
    assert [e[0] for e in events] == ["speech_started", "utterance"]
    assert len(predictor.calls) == 2  # ended right after the second (0.9) call
    assert not d.in_speech


def test_turn_predictor_falls_back_to_ceiling_when_score_stays_low():
    predictor = ScriptedPredictor([0.1] * 10)
    d = _detector(
        [True] + [False] * 10,
        turn_predictor=predictor,
        min_silence_ms=64,
        max_silence_ms=320,
        predictor_every_ms=64,
    )
    events = [e for i in range(11) for e in d.feed(bytes([i]) * 1024)]
    assert [e[0] for e in events] == ["speech_started", "utterance"]
    assert len(predictor.calls) == 5  # trailing frames 2,4,6,8,10


def test_no_turn_predictor_uses_silence_ms_as_today():
    d = _detector([True, True, False, False, False], turn_predictor=None)
    frames = [bytes([i]) * 1024 for i in range(5)]
    events = [e for f in frames for e in d.feed(f)]
    assert [e[0] for e in events] == ["speech_started", "utterance"]


class ExplodingPredictor:
    def __init__(self):
        self.calls = 0

    def is_complete(self, pcm):
        self.calls += 1
        raise RuntimeError("onnx session died")


def test_turn_predictor_exception_falls_back_to_ceiling_and_warns_once(caplog):
    predictor = ExplodingPredictor()
    d = _detector(
        [True] + [False] * 10,
        turn_predictor=predictor,
        min_silence_ms=64,
        max_silence_ms=320,
        predictor_every_ms=64,
    )
    with caplog.at_level("WARNING", logger="richard.realtime.vad"):
        events = [e for i in range(11) for e in d.feed(bytes([i]) * 1024)]

    assert [e[0] for e in events] == ["speech_started", "utterance"]
    assert not d.in_speech
    assert predictor.calls == 5  # trailing frames 2,4,6,8,10 all raise
    warnings = [r for r in caplog.records if r.levelname == "WARNING"]
    assert len(warnings) == 1  # warned once despite 5 failures
    assert "turn_predictor.is_complete failed" in warnings[0].message


def test_predictor_can_end_at_the_silence_floor_when_min_silence_ms_unset():
    # silence_ms=96 -> floor 3 frames. min_silence_ms left unset, so the
    # predictor floor matches the no-predictor floor exactly (never lower).
    predictor = ScriptedPredictor([0.9])
    d = _detector(
        [True, False, False, False],
        turn_predictor=predictor,
        max_silence_ms=320,
        predictor_every_ms=64,
    )
    events = [e for i in range(4) for e in d.feed(bytes([i]) * 1024)]
    assert [e[0] for e in events] == ["speech_started", "utterance"]
    assert len(predictor.calls) == 1  # first poll at trailing==3 (the floor)
    assert events[1][2]["reason"] == "predictor"
    assert events[1][2]["score"] == 0.9


def test_predictor_below_threshold_waits_for_ceiling():
    # threshold defaults to 0.8; a 0.7 score must not end the turn early —
    # it waits for the max_silence_ms ceiling instead.
    predictor = ScriptedPredictor([0.7, 0.7, 0.7, 0.7, 0.7])
    d = _detector(
        [True] + [False] * 10,
        turn_predictor=predictor,
        min_silence_ms=64,
        max_silence_ms=320,
        predictor_every_ms=64,
    )
    events = [e for i in range(11) for e in d.feed(bytes([i]) * 1024)]
    assert [e[0] for e in events] == ["speech_started", "utterance"]
    assert events[1][2]["reason"] == "ceiling"
    assert events[1][2]["score"] == 0.7


def test_endpoint_decision_is_logged_at_info(caplog):
    d = _detector([True, True, False, False, False], turn_predictor=None)
    frames = [bytes([i]) * 1024 for i in range(5)]
    with caplog.at_level("INFO", logger="richard.realtime"):
        for f in frames:
            d.feed(f)
    records = [r for r in caplog.records if r.name == "richard.realtime"]
    assert len(records) == 1
    message = records[0].getMessage()
    assert message.startswith("endpoint: reason=silence trailing_ms=")
    assert "score=none" in message
    assert "polls=0" in message


def test_ensure_smart_turn_skips_download_when_present(tmp_path):
    target = tmp_path / "smart-turn-v3.2-cpu.onnx"
    target.write_bytes(b"x" * 2048)  # > 1 KiB guard, mirrors _present
    assert ensure_smart_turn(models_dir=tmp_path, client=None) == target


def test_ensure_smart_turn_downloads_when_missing(tmp_path):
    class FakeClient:
        def get(self, url):
            class R:
                content = b"o" * 4096

                def raise_for_status(self):
                    pass

            return R()

    path = ensure_smart_turn(models_dir=tmp_path, client=FakeClient(), write=lambda *_: None)
    assert path.read_bytes() == b"o" * 4096
