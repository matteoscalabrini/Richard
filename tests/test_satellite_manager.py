from richard.brain.completion import StreamEvent, ToolCall
from richard.config import Personality
from richard.memory import MemoryStore
from richard.providers.memory import MemoryProvider
from richard.satellite.manager import SatelliteManager
from richard.satellite.protocol import State
from richard.satellite.relays import RelayRegistry
from tests.satellite_fakes import FakeRelayConnection


class FakeBrain:
    def __init__(self, scripts):
        self.scripts = list(scripts)

    def stream(self, messages, tools=None):
        script = self.scripts.pop(0)
        for delta in script.get("deltas", []):
            yield StreamEvent(delta=delta)
        yield StreamEvent(tool_calls=script.get("tool_calls", []), done=True)

    def complete(self, *a, **k):
        raise AssertionError("unused")

    def chat(self, *a, **k):
        return ""


class FakeSTT:
    def __init__(self, text):
        self._text = text

    def transcribe(self, pcm, samplerate=16000):
        return self._text


class FakeSynth:
    # At the 16 kHz link rate so the manager forwards synth output unchanged (the
    # synth payloads here are readable text sentinels, not PCM). The 24->16 kHz
    # resample path is covered by tests/test_audio_resample.py.
    samplerate = 16000

    def synth(self, text):
        return f"AUDIO:{text}".encode()


class AllSpeechVad:
    """webrtcvad stand-in: every frame is speech, so record_utterance returns all frames."""

    def is_speech(self, frame, samplerate):
        return True


def _manager(brain, stt, relays):
    return SatelliteManager(
        brain=brain,
        memory_provider=MemoryProvider(MemoryStore(":memory:")),
        personality=Personality(), stt=stt, synthesizer=FakeSynth(),
        relays=relays, vad_factory=AllSpeechVad, samplerate=16000,
    )


def test_turn_plain_reply_streams_states_and_audio():
    conn = FakeRelayConnection(inbound_audio=[b"\x00" * 640, b"\x00" * 640])
    relays = RelayRegistry(); relays.register("HUB1", conn)
    mgr = _manager(FakeBrain([{"deltas": ["Hi there. ", "All good."]}]), FakeSTT("hello"), relays)

    mgr.run_turn(conn, room_name="Kitchen")

    states = [m.state for m in conn.sent if isinstance(m, State)]
    assert states == ["listening", "thinking", "speaking", "idle"]
    assert conn.sent_audio == [b"AUDIO:Hi there.", b"AUDIO:All good."]


def test_turn_empty_transcript_goes_idle_without_speaking():
    conn = FakeRelayConnection(inbound_audio=[b"\x00" * 640])
    relays = RelayRegistry(); relays.register("HUB1", conn)
    mgr = _manager(FakeBrain([]), FakeSTT(""), relays)

    mgr.run_turn(conn, room_name="Kitchen")

    states = [m.state for m in conn.sent if isinstance(m, State)]
    assert states == ["listening", "thinking", "idle"]
    assert conn.sent_audio == []


def test_turn_can_use_an_extra_provider():
    class ExtraProvider:
        def __init__(self):
            self.calls = []

        def schemas(self):
            return [
                {
                    "type": "function",
                    "function": {
                        "name": "extra_action",
                        "description": "test",
                        "parameters": {"type": "object", "properties": {}},
                    },
                }
            ]

        def context(self):
            return "An extra integration is connected."

        def execute(self, name, arguments):
            self.calls.append((name, arguments))
            return "extra complete"

    extra = ExtraProvider()
    brain = FakeBrain(
        [
            {"tool_calls": [ToolCall(id="ha1", name="extra_action", arguments={})]},
            {"deltas": ["Done."]},
        ]
    )
    conn = FakeRelayConnection(inbound_audio=[b"\x00" * 640])
    relays = RelayRegistry(); relays.register("HUB1", conn)
    mgr = SatelliteManager(
        brain=brain,
        memory_provider=MemoryProvider(MemoryStore(":memory:")),
        personality=Personality(),
        stt=FakeSTT("do it"),
        synthesizer=FakeSynth(),
        relays=relays,
        vad_factory=AllSpeechVad,
        extra_providers=[extra],
    )

    mgr.run_turn(conn, room_name="Kitchen")

    assert extra.calls == [("extra_action", {})]
    assert conn.sent_audio == [b"AUDIO:Done."]


def test_turn_speaks_error_and_returns_idle_when_brain_unreachable():
    from richard.errors import BrainUnreachable

    class UnreachableBrain:
        def stream(self, messages, tools=None):
            raise BrainUnreachable("nope")

        def complete(self, *a, **k):
            raise AssertionError("unused")

        def chat(self, *a, **k):
            return ""

    conn = FakeRelayConnection(inbound_audio=[b"\x00" * 640])
    relays = RelayRegistry(); relays.register("HUB1", conn)
    mgr = _manager(UnreachableBrain(), FakeSTT("hello"), relays)

    mgr.run_turn(conn, room_name="Kitchen")

    states = [m.state for m in conn.sent if isinstance(m, State)]
    assert states == ["listening", "thinking", "speaking", "idle"]
    assert conn.sent_audio == [b"AUDIO:I can't reach my brain right now."]


def test_carry_over_seeds_next_turn_within_window():
    clock = {"t": 0.0}
    conn = FakeRelayConnection(inbound_audio=[b"\x00" * 640])
    relays = RelayRegistry(); relays.register("HUB1", conn)
    brain = FakeBrain([{"deltas": ["Sure."]}, {"deltas": ["Done."]}])
    mgr = SatelliteManager(
        brain=brain,
        memory_provider=MemoryProvider(MemoryStore(":memory:")),
        personality=Personality(), stt=FakeSTT("first thing"), synthesizer=FakeSynth(),
        relays=relays, vad_factory=AllSpeechVad, samplerate=16000,
        clock=lambda: clock["t"],
    )
    mgr.run_turn(conn, room_name="Kitchen")

    # Second turn 10s later, different transcript; capture what the brain sees.
    clock["t"] = 10.0
    mgr._stt = FakeSTT("second thing")
    seen = {}
    orig = brain.stream
    def spy(messages, tools=None):
        seen["messages"] = list(messages)
        return orig(messages, tools)
    brain.stream = spy
    conn2 = FakeRelayConnection(inbound_audio=[b"\x00" * 640]); relays.register("HUB1", conn2)
    mgr.run_turn(conn2, room_name="Bedroom")

    contents = [m["content"] for m in seen["messages"]]
    assert "first thing" in contents      # prior user turn carried over
    assert "Sure." in contents            # prior assistant turn carried over
    assert "second thing" in contents     # current turn present


def test_carry_over_expires_after_window():
    clock = {"t": 0.0}
    conn = FakeRelayConnection(inbound_audio=[b"\x00" * 640])
    relays = RelayRegistry(); relays.register("HUB1", conn)
    brain = FakeBrain([{"deltas": ["Sure."]}, {"deltas": ["Done."]}])
    mgr = SatelliteManager(
        brain=brain,
        memory_provider=MemoryProvider(MemoryStore(":memory:")),
        personality=Personality(), stt=FakeSTT("first thing"), synthesizer=FakeSynth(),
        relays=relays, vad_factory=AllSpeechVad, samplerate=16000,
        clock=lambda: clock["t"], carry_window_s=180.0,
    )
    mgr.run_turn(conn, room_name="Kitchen")

    clock["t"] = 10_000.0  # far beyond the window
    mgr._stt = FakeSTT("much later")
    seen = {}
    orig = brain.stream
    brain.stream = lambda messages, tools=None: (seen.__setitem__("messages", list(messages)), orig(messages, tools))[1]
    conn2 = FakeRelayConnection(inbound_audio=[b"\x00" * 640]); relays.register("HUB1", conn2)
    mgr.run_turn(conn2, room_name="Kitchen")

    contents = [m["content"] for m in seen["messages"]]
    assert "first thing" not in contents
    assert "much later" in contents
