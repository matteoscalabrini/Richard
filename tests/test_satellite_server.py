from richard.config import Config, Satellite
from richard.satellite.protocol import State, encode
from richard.satellite.server import WsRelayConnection


def test_config_has_satellite_defaults():
    s = Config().satellite
    assert s.enabled is False
    assert s.host == "0.0.0.0"
    assert s.port == 8770


class FakeWs:
    """Minimal stand-in: sync text + binary sinks (the real serve() supplies a loop-marshaling
    adapter with the same two methods)."""

    def __init__(self):
        self.outbox_text = []
        self.outbox_bin = []

    def send_text(self, data):
        self.outbox_text.append(data)

    def send_bytes(self, data):
        self.outbox_bin.append(data)


def test_ws_relay_connection_send_encodes_control_and_audio():
    ws = FakeWs()
    conn = WsRelayConnection(ws)
    conn.send(State(state="listening"))
    conn.send_audio(b"PCM")
    assert ws.outbox_text == [encode(State(state="listening"))]
    assert ws.outbox_bin == [b"PCM"]


def test_send_audio_chunks_large_frames_sample_aligned():
    ws = FakeWs()
    conn = WsRelayConnection(ws)
    conn.AUDIO_CHUNK_PACE_S = 0  # shadow the class attr: no real-time pacing in tests
    payload = bytes(range(256)) * 300  # 76800 bytes ≈ 2.4 s of 16 kHz mono int16
    conn.send_audio(payload)
    assert len(ws.outbox_bin) == 20  # ceil(76800 / 4000)
    assert all(len(c) <= WsRelayConnection.AUDIO_CHUNK_BYTES for c in ws.outbox_bin)
    assert all(len(c) % 2 == 0 for c in ws.outbox_bin)  # int16 sample alignment
    assert b"".join(ws.outbox_bin) == payload


import asyncio

from richard.brain.completion import StreamEvent
from richard.config import Personality
from richard.memory import MemoryStore
from richard.providers.memory import MemoryProvider
from richard.satellite.manager import SatelliteManager
from richard.satellite.protocol import Hello, SessionStart, decode
from richard.satellite.relays import RelayRegistry
from richard.satellite.server import handle_connection


class _E2EBrain:
    def stream(self, messages, tools=None):
        yield StreamEvent(delta="Hi.")
        yield StreamEvent(done=True)

    def complete(self, *a, **k):
        raise AssertionError("unused")

    def chat(self, *a, **k):
        return ""


class _E2ESTT:
    def transcribe(self, pcm, samplerate=16000):
        return "hello"


class _E2ESynth:
    # 16 kHz link rate -> manager forwards synth output unchanged (payloads are text
    # sentinels, not PCM). The 24->16 kHz resample is covered by test_audio_resample.py.
    samplerate = 16000

    def synth(self, text):
        return b"AUDIO:" + text.encode()


class _FrameVad:
    # speech frames start with 0x01; silence frames are 0x00.
    def is_speech(self, frame, samplerate):
        return frame[:1] == b"\x01"


class _FakeAsyncWs:
    def __init__(self, incoming):
        self._incoming = list(incoming)
        self.sent = []

    def __aiter__(self):
        return self._gen()

    async def _gen(self):
        for frame in self._incoming:
            yield frame
            await asyncio.sleep(0)  # let the concurrent turn run

    async def send(self, data):
        self.sent.append(data)


def test_serve_handles_a_full_turn_without_deadlock():
    mgr = SatelliteManager(
        brain=_E2EBrain(),
        memory_provider=MemoryProvider(MemoryStore(":memory:")),
        personality=Personality(), stt=_E2ESTT(), synthesizer=_E2ESynth(),
        relays=RelayRegistry(), vad_factory=_FrameVad, samplerate=16000,
    )
    speech = b"\x01" * 640
    silence = b"\x00" * 640
    # 1 speech frame then 40 silence frames (= record_utterance's 800ms/20ms silence gate).
    frames = [
        encode(Hello(relay_id="HUB1", room_id="r1", room_name="Kitchen", capabilities=["voice"])),
        encode(SessionStart()),
        speech,
    ] + [silence] * 40
    ws = _FakeAsyncWs(frames)

    asyncio.run(asyncio.wait_for(handle_connection(mgr, ws), timeout=5))

    sent_msgs = [decode(d) for d in ws.sent if isinstance(d, str)]
    states = [m.state for m in sent_msgs if isinstance(m, State)]
    assert states[0] == "listening"
    assert states[-1] == "idle"
    sent_audio = [d for d in ws.sent if isinstance(d, (bytes, bytearray))]
    assert b"AUDIO:Hi." in sent_audio
