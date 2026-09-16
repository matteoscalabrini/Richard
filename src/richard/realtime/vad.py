"""Silero VAD v5 (ONNX) for the realtime pipeline.

Replaces webrtcvad for realtime sessions: far better speech/noise discrimination,
which is what lets the endpoint silence window drop to ~400 ms without cutting
people off. Model is a ~2 MB ONNX file downloaded on first use (same guard
pattern as voice.tts.ensure_kokoro). Runs on CPU; inference is ~1 ms per frame.
"""
from __future__ import annotations

import logging
from collections import deque
from pathlib import Path

import numpy as np

logger = logging.getLogger("richard.realtime")

SILERO_URL = (
    "https://github.com/snakers4/silero-vad/raw/v5.1.2/src/silero_vad/data/silero_vad.onnx"
)
SILERO_FILE = "silero_vad.onnx"
SMART_TURN_URL = (
    "https://huggingface.co/pipecat-ai/smart-turn-v3/resolve/main/smart-turn-v3.2-cpu.onnx"
)
SMART_TURN_FILE = "smart-turn-v3.2-cpu.onnx"
FRAME_SAMPLES = 512  # Silero v5 accepts exactly 512-sample frames at 16 kHz (32 ms)
FRAME_BYTES = FRAME_SAMPLES * 2  # PCM16


def default_models_dir() -> Path:
    return Path.home() / ".richard" / "models"


def _present(path: Path) -> bool:
    # Guard against truncated downloads (same rule as voice.tts._present).
    return path.exists() and path.stat().st_size > 1024


def ensure_silero(*, models_dir: Path | None = None, client=None, write=print) -> Path:
    models_dir = models_dir or default_models_dir()
    models_dir.mkdir(parents=True, exist_ok=True)
    target = models_dir / SILERO_FILE
    if _present(target):
        return target
    if client is None:
        import httpx

        client = httpx.Client(timeout=120.0, follow_redirects=True)
    write(f"Downloading {SILERO_FILE} from GitHub...")
    try:
        resp = client.get(SILERO_URL)
        resp.raise_for_status()
    except Exception as exc:
        raise RuntimeError(f"Failed to download Silero VAD from {SILERO_URL}: {exc}") from exc
    target.write_bytes(resp.content)
    return target


def ensure_smart_turn(*, models_dir: Path | None = None, client=None, write=print) -> Path:
    models_dir = models_dir or default_models_dir()
    models_dir.mkdir(parents=True, exist_ok=True)
    target = models_dir / SMART_TURN_FILE
    if _present(target):
        return target
    if client is None:
        import httpx

        client = httpx.Client(timeout=120.0, follow_redirects=True)
    write(f"Downloading {SMART_TURN_FILE} from Hugging Face...")
    try:
        resp = client.get(SMART_TURN_URL)
        resp.raise_for_status()
    except Exception as exc:
        raise RuntimeError(f"Failed to download smart-turn from {SMART_TURN_URL}: {exc}") from exc
    target.write_bytes(resp.content)
    return target


class SileroVAD:
    """Streaming speech probability per FRAME_SAMPLES PCM16 frame.

    Stateful: the model carries an RNN state across frames, so one instance
    serves one audio stream. reset() between utterances."""

    def __init__(self, model_path, *, threshold: float = 0.5, _session=None) -> None:
        self._model_path = str(model_path)
        self._threshold = threshold
        self._session = _session
        self._state = np.zeros((2, 1, 128), dtype=np.float32)

    def _ensure(self):
        if self._session is None:
            import onnxruntime

            self._session = onnxruntime.InferenceSession(
                self._model_path, providers=["CPUExecutionProvider"]
            )
        return self._session

    def is_speech(self, frame: bytes, samplerate: int = 16000) -> bool:
        audio = np.frombuffer(frame, dtype=np.int16).astype(np.float32) / 32768.0
        outputs = self._ensure().run(
            None,
            {
                "input": audio[np.newaxis, :],
                "state": self._state,
                "sr": np.array(samplerate, dtype=np.int64),
            },
        )
        prob, self._state = outputs[0], outputs[1]
        return float(np.asarray(prob).reshape(-1)[0]) >= self._threshold

    def reset(self) -> None:
        self._state = np.zeros((2, 1, 128), dtype=np.float32)


class EndpointDetector:
    """Continuous endpointing over a live frame stream.

    Unlike voice.vad.record_utterance (blocking, one utterance per call) this is
    fed frame-by-frame forever — the realtime session's front stage. Trailing
    silence stays in the utterance (Whisper handles it; cutting it clips words).
    """

    def __init__(self, vad, *, samplerate: int = 16000, frame_ms: int = 32,
                 silence_ms: int = 400, preroll_ms: int = 1000, max_ms: int = 30000,
                 turn_predictor=None, min_silence_ms: int | None = None, max_silence_ms: int = 1200,
                 predictor_every_ms: int = 160, threshold: float = 0.8) -> None:
        self._vad = vad
        self._samplerate = samplerate
        self._frame_ms = frame_ms
        self._silence_frames = max(1, silence_ms // frame_ms)
        self._max_frames = max(1, max_ms // frame_ms)
        self._preroll: deque[bytes] = deque(maxlen=max(1, preroll_ms // frame_ms))
        self._collected = bytearray()
        self._in_speech = False
        self._trailing = 0
        self._frames = 0
        self._turn_predictor = turn_predictor
        # The predictor floor is never lower than the plain-silence floor: with no
        # predictor a turn always ends at _silence_frames, so a predictor must not
        # be able to end it sooner — only extend it (up to max_silence_ms).
        if min_silence_ms is None:
            self._min_silence_frames = self._silence_frames
        else:
            self._min_silence_frames = max(1, min_silence_ms // frame_ms)
        self._max_silence_frames = max(1, max_silence_ms // frame_ms)
        self._predictor_every_frames = max(1, predictor_every_ms // frame_ms)
        self._threshold = threshold
        self._predictor_warned = False
        self._predictor_polls = 0

    @property
    def in_speech(self) -> bool:
        return self._in_speech

    def collected(self) -> bytes:
        return bytes(self._collected)

    def feed(self, frame: bytes) -> list[tuple]:
        events: list[tuple] = []
        speech = self._vad.is_speech(frame, self._samplerate)
        if not self._in_speech:
            if not speech:
                self._preroll.append(frame)
                return events
            self._in_speech = True
            self._trailing = 0
            self._frames = 0
            self._predictor_polls = 0
            self._collected = bytearray(b"".join(self._preroll) + frame)
            self._preroll.clear()
            events.append(("speech_started",))
            return events
        self._collected += frame
        self._frames += 1
        self._trailing = 0 if speech else self._trailing + 1
        reason = None
        score = None
        if self._frames >= self._max_frames:
            reason = "max_length"
        elif self._turn_predictor is not None:
            if (
                self._trailing >= self._min_silence_frames
                and (self._trailing - self._min_silence_frames) % self._predictor_every_frames == 0
            ):
                try:
                    score = self._turn_predictor.is_complete(bytes(self._collected[-256000:]))
                    self._predictor_polls += 1
                    if score >= self._threshold:
                        reason = "predictor"
                except Exception:
                    if not self._predictor_warned:
                        self._predictor_warned = True
                        import logging

                        logging.getLogger(__name__).warning(
                            "turn_predictor.is_complete failed; falling back to silence ceiling",
                            exc_info=True,
                        )
            if reason is None and self._trailing >= self._max_silence_frames:
                reason = "ceiling"
        elif self._trailing >= self._silence_frames:
            reason = "silence"
        if reason is not None:
            trailing_ms = self._trailing * self._frame_ms
            logger.info(
                "endpoint: reason=%s trailing_ms=%d score=%s polls=%d",
                reason,
                trailing_ms,
                "none" if score is None else score,
                self._predictor_polls,
            )
            events.append((
                "utterance",
                bytes(self._collected),
                {
                    "reason": reason,
                    "trailing_ms": trailing_ms,
                    "score": score,
                    "polls": self._predictor_polls,
                },
            ))
            self._in_speech = False
            self._collected = bytearray()
            if hasattr(self._vad, "reset"):
                self._vad.reset()
        return events
