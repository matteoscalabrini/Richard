import threading
import time

from richard.realtime.session import RealtimeSession

FRAME = b"\x00" * 1024  # one 512-sample PCM16 frame


class ScriptedDetector:
    """Replays a fixed event script; frame content is irrelevant."""

    def __init__(self, script):
        self.script = list(script)  # one list of event-tuples per feed() call
        self.in_speech = False
        self.fed = 0

    def feed(self, frame):
        self.fed += 1
        events = self.script.pop(0) if self.script else []
        for e in events:
            if e[0] == "speech_started":
                self.in_speech = True
            if e[0] == "utterance":
                self.in_speech = False
        return events

    def collected(self):
        return b"so-far"


class FakeTranscriber:
    def __init__(self, text="turn the fan on"):
        self.text = text

    def partial(self, pcm):
        return "turn the"

    def final(self, pcm):
        return self.text


class FakeEngine:
    def __init__(self, deltas=("Sure thing, ", "the fan is on now.")):
        self.deltas = deltas
        self.seen = []

    def respond_streaming(self, conversation):
        self.seen.append([ (m.role, m.content) for m in conversation.history() ])
        yield from self.deltas


class FakeTTS:
    samplerate = 24000

    def __init__(self):
        self.spoken = []

    def synth(self, text):
        self.spoken.append(text)
        return b"\x11\x22"


def collect_session(**overrides):
    emitted = []
    done = threading.Event()

    def emit(event):
        emitted.append(event)
        if event["type"] == "response.done":
            done.set()

    kw = dict(
        engine=FakeEngine(),
        transcriber=FakeTranscriber(),
        tts=FakeTTS(),
        detector=ScriptedDetector([[("speech_started",)], [("utterance", b"pcm")]]),
        emit=emit,
    )
    kw.update(overrides)
    return RealtimeSession(**kw), emitted, done


def wait(done, timeout=5.0):
    assert done.wait(timeout), "response.done never arrived"


def wait_until(predicate, timeout=5.0):
    """Poll predicate() until true. Used to synchronize with the audio worker
    thread, which only processes a fed frame asynchronously off a queue —
    without this, a test that releases a gated fake immediately after
    feed_audio() races the worker thread and is flaky."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.001)
    return False


def test_full_turn_emits_protocol_sequence():
    session, emitted, done = collect_session()
    session.feed_audio(FRAME)
    session.feed_audio(FRAME)
    wait(done)
    kinds = [e["type"] for e in emitted]
    assert kinds[0] == "input_audio_buffer.speech_started"
    assert "input_audio_buffer.speech_stopped" in kinds
    assert "conversation.item.input_audio_transcription.completed" in kinds
    assert kinds.index("response.created") < kinds.index("response.output_text.delta")
    assert "response.audio.delta" in kinds
    assert kinds[-1] == "response.done"
    assert emitted[-1]["response"]["status"] == "completed"
    session.close()


def test_conversation_carries_history():
    session, emitted, done = collect_session()
    session.feed_audio(FRAME)
    session.feed_audio(FRAME)
    wait(done)
    roles = [m.role for m in session.conversation.history()]
    assert roles == ["user", "assistant"]
    session.close()


def test_tts_receives_progressive_chunks():
    tts = FakeTTS()
    session, emitted, done = collect_session(tts=tts)
    session.feed_audio(FRAME)
    session.feed_audio(FRAME)
    wait(done)
    assert tts.spoken[0] == "Sure thing,"  # first clause, fast
    assert "".join(tts.spoken).replace(" ", "") == "Surething,thefanisonnow.".replace(" ", "")
    session.close()


def test_partial_transcripts_emitted_during_speech():
    # speech starts, then 30 in-speech frames (no new events), then endpoint
    script = [[("speech_started",)]] + [[] for _ in range(30)] + [[("utterance", b"pcm")]]
    session, emitted, done = collect_session(
        detector=ScriptedDetector(script), partial_every=10
    )
    for _ in range(32):
        session.feed_audio(FRAME)
    wait(done)
    deltas = [e for e in emitted if e["type"] == "conversation.item.input_audio_transcription.delta"]
    assert len(deltas) >= 2
    assert deltas[0]["delta"] == "turn the"
    session.close()


def test_text_item_runs_a_turn_without_stt():
    session, emitted, done = collect_session(detector=ScriptedDetector([]))
    session.create_text_item("hello richard")
    wait(done)
    kinds = [e["type"] for e in emitted]
    assert "conversation.item.input_audio_transcription.completed" not in kinds
    assert "response.audio.delta" in kinds
    session.close()


def test_empty_transcript_returns_to_listening_silently():
    session, emitted, done = collect_session(transcriber=FakeTranscriber(text=""))
    session.feed_audio(FRAME)
    session.feed_audio(FRAME)
    time.sleep(0.3)
    assert not any(e["type"] == "response.created" for e in emitted)
    assert session.state == "listening"
    session.close()


def test_short_frames_are_rebuffered_to_512_samples():
    detector = ScriptedDetector([[("speech_started",)], [("utterance", b"pcm")]])
    session, emitted, done = collect_session(detector=detector)
    half = b"\x00" * 512
    for _ in range(4):  # four half-frames == two full frames
        session.feed_audio(half)
    wait(done)
    assert detector.fed == 2
    session.close()


class GatedTTS(FakeTTS):
    """Blocks inside synth until released — holds the session in 'speaking'."""

    def __init__(self):
        super().__init__()
        self.entered = threading.Event()
        self.release = threading.Event()

    def synth(self, text):
        self.entered.set()
        assert self.release.wait(5)
        return super().synth(text)


def test_vad_barge_in_truncates_response():
    tts = GatedTTS()
    detector = ScriptedDetector([
        [("speech_started",)], [("utterance", b"pcm")],  # first turn
        [("speech_started",)],                            # barge-in while speaking
    ])
    session, emitted, done = collect_session(tts=tts, detector=detector)
    session.feed_audio(FRAME)
    session.feed_audio(FRAME)
    assert tts.entered.wait(5)          # turn is mid-synthesis → state 'speaking'
    session.feed_audio(FRAME)           # user speaks over Richard
    # feed_audio() only enqueues; wait for the audio worker thread to actually
    # process the frame and register the barge-in before releasing the gate —
    # otherwise this races the turn thread's interrupt check and is flaky.
    assert wait_until(lambda: session._interrupt.is_set())
    tts.release.set()
    wait(done)
    kinds = [e["type"] for e in emitted]
    assert "conversation.item.truncated" in kinds
    assert emitted[-1]["response"]["status"] == "cancelled"
    session.close()


def test_barge_in_off_is_deaf_while_turn_runs():
    tts = GatedTTS()
    detector = ScriptedDetector([
        [("speech_started",)], [("utterance", b"pcm")],
        [("speech_started",)],  # would be a barge-in; must never reach the detector
    ])
    session, emitted, done = collect_session(tts=tts, detector=detector, barge_in="off")
    session.feed_audio(FRAME)
    session.feed_audio(FRAME)
    assert tts.entered.wait(5)
    session.feed_audio(FRAME)   # dropped: worker is deaf outside 'listening'
    # Wait for the audio worker to actually dequeue and drop the frame before
    # releasing the gate — the frame never reaches the detector in this mode,
    # so draining the queue (rather than an interrupt/detector signal) is the
    # only observable proof the worker has processed it.
    assert wait_until(lambda: session._frames_q.qsize() == 0)
    tts.release.set()
    wait(done)
    assert detector.fed == 2
    assert emitted[-1]["response"]["status"] == "completed"
    session.close()


def test_explicit_cancel_stops_response():
    tts = GatedTTS()
    session, emitted, done = collect_session(tts=tts)
    session.feed_audio(FRAME)
    session.feed_audio(FRAME)
    assert tts.entered.wait(5)
    session.cancel_response()
    tts.release.set()
    wait(done)
    assert emitted[-1]["response"]["status"] == "cancelled"
    session.close()


class DownEngine:
    def respond_streaming(self, conversation):
        from richard.errors import BrainUnreachable
        raise BrainUnreachable("boom")
        yield  # pragma: no cover — makes this a generator


def test_brain_unreachable_speaks_fallback_and_fails_response():
    tts = FakeTTS()
    session, emitted, done = collect_session(engine=DownEngine(), tts=tts)
    session.feed_audio(FRAME)
    session.feed_audio(FRAME)
    wait(done)
    assert tts.spoken == ["I can't reach my brain right now."]
    assert emitted[-1]["response"]["status"] == "failed"
    assert any(e["type"] == "error" and e["error"]["code"] == "brain_unreachable" for e in emitted)
    assert session.state == "listening"  # session survives
    session.close()


class FlakyTTS(FakeTTS):
    def synth(self, text):
        if not self.spoken:
            self.spoken.append("(failed)")
            raise RuntimeError("synth exploded")
        return super().synth(text)


def test_tts_chunk_failure_skips_only_that_chunk():
    tts = FlakyTTS()
    session, emitted, done = collect_session(tts=tts)
    session.feed_audio(FRAME)
    session.feed_audio(FRAME)
    wait(done)
    assert any(e["type"] == "error" and e["error"]["code"] == "tts_error" for e in emitted)
    assert emitted[-1]["response"]["status"] == "completed"
    assert len(tts.spoken) >= 2  # the later chunk still went out
    session.close()


class GatedTranscriber(FakeTranscriber):
    """Blocks inside final() until released — holds the turn in 'thinking'."""

    def __init__(self, text="hello richard"):
        super().__init__(text)
        self.entered = threading.Event()
        self.release = threading.Event()

    def final(self, pcm):
        self.entered.set()
        assert self.release.wait(5)
        return self.text


def test_cancel_during_thinking_is_not_lost():
    tr = GatedTranscriber()
    session, emitted, done = collect_session(transcriber=tr)
    session.feed_audio(FRAME)
    session.feed_audio(FRAME)
    assert tr.entered.wait(5)   # turn thread is inside final() → 'thinking'
    session.cancel_response()
    tr.release.set()
    wait(done)
    assert emitted[-1]["response"]["status"] == "cancelled"
    session.close()


class ExplodingEngine:
    def respond_streaming(self, conversation):
        raise RuntimeError("kaboom")
        yield  # pragma: no cover — makes this a generator


def test_engine_crash_fails_response_and_survives():
    session, emitted, done = collect_session(engine=ExplodingEngine())
    session.feed_audio(FRAME)
    session.feed_audio(FRAME)
    wait(done)
    assert emitted[-1]["response"]["status"] == "failed"
    assert any(e["type"] == "error" and e["error"]["code"] == "engine_error" for e in emitted)
    assert session.state == "listening"
    session.close()


def test_engine_crash_speaks_error_line():
    # An engine failure must be audible, not just an error event the web
    # client hides in its console — silence reads as a crash to the user.
    tts = FakeTTS()
    session, emitted, done = collect_session(engine=ExplodingEngine(), tts=tts)
    session.feed_audio(FRAME)
    session.feed_audio(FRAME)
    wait(done)
    assert tts.spoken == ["Something went wrong on my end."]
    session.close()


def test_audio_worker_survives_stage_crash():
    class ExplodingDetector(ScriptedDetector):
        def feed(self, frame):
            raise RuntimeError("onnx died")

    emitted = []
    session = RealtimeSession(
        engine=FakeEngine(), transcriber=FakeTranscriber(), tts=FakeTTS(),
        detector=ExplodingDetector([]), emit=emitted.append,
    )
    session.feed_audio(FRAME)
    assert wait_until(lambda: any(e["type"] == "error" for e in emitted))
    assert [e["error"]["code"] for e in emitted if e["type"] == "error"] == ["internal_error"]
    session.feed_audio(FRAME)   # further frames: dropped, no second error
    time.sleep(0.1)
    assert len([e for e in emitted if e["type"] == "error"]) == 1
    session.close()
