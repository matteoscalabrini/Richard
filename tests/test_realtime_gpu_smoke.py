"""Real-model smoke, excluded from the default run (pytest -m gpu to include).

Verifies the two model integrations mocked everywhere else: the Silero ONNX
contract (input/state/sr tensor names, 512-sample frames) and a faster-whisper
decode round-trip. Uses the tiny Whisper model so the download is small; runs
on CPU too — 'gpu' marks it as heavy/networked, not CUDA-only.

Note on fidelity: the unit tests' FakeOrtSession does not validate the input/sr
feed keys — this smoke test is the real check that ensures the correct tensor
names are used in the actual Silero VAD integration.
"""
import numpy as np
import pytest

pytestmark = pytest.mark.gpu


def test_silero_onnx_contract():
    from richard.realtime.vad import FRAME_SAMPLES, SileroVAD, ensure_silero

    vad = SileroVAD(ensure_silero(write=lambda *_: None))
    silence = np.zeros(FRAME_SAMPLES, dtype=np.int16).tobytes()
    assert vad.is_speech(silence) is False
    vad.reset()


def test_turn_transcriber_decodes_silence_to_empty():
    from richard.realtime.stt import TurnTranscriber

    t = TurnTranscriber("tiny", device="cpu", compute_type="int8")
    assert t.final(np.zeros(16000, dtype=np.int16).tobytes()) == ""
