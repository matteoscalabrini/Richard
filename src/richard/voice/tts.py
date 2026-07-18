from __future__ import annotations

import queue
import sys
import threading
from pathlib import Path

HF_BASE = "https://huggingface.co/olivierdion007/TARS-AI/resolve/main"

# Kokoro-82M int8 ONNX (higher quality than Piper, still CPU/Pi-capable).
_KOKORO_RELEASE = "https://github.com/thewh1teagle/kokoro-onnx/releases/download/model-files-v1.0"
KOKORO_MODEL_FILE = "kokoro-v1.0.int8.onnx"
KOKORO_VOICES_FILE = "voices-v1.0.bin"


def default_voices_dir() -> Path:
    return Path.home() / ".richard" / "voices"


def _present(path: Path) -> bool:
    # Guard against Git-LFS pointers / truncated downloads.
    return path.exists() and path.stat().st_size > 1024


def ensure_voice(name: str = "TARS", *, voices_dir: Path | None = None, client=None, write=print) -> Path:
    voices_dir = voices_dir or default_voices_dir()
    voices_dir.mkdir(parents=True, exist_ok=True)
    onnx = voices_dir / f"{name}.onnx"
    cfg = voices_dir / f"{name}.onnx.json"
    if _present(onnx) and _present(cfg):
        return onnx
    if client is None:
        import httpx

        client = httpx.Client(timeout=120.0, follow_redirects=True)
    for path, url in [(onnx, f"{HF_BASE}/{name}.onnx"), (cfg, f"{HF_BASE}/{name}.onnx.json")]:
        if _present(path):
            continue
        write(f"Downloading {path.name} from Hugging Face...")
        try:
            resp = client.get(url)
            resp.raise_for_status()
        except Exception as exc:
            raise RuntimeError(f"Failed to download voice model from {url}: {exc}") from exc
        path.write_bytes(resp.content)
    return onnx


def ensure_kokoro(*, voices_dir: Path | None = None, client=None, write=print) -> tuple[Path, Path]:
    """Download the Kokoro int8 model + voices pack on first use; return their paths."""
    voices_dir = voices_dir or default_voices_dir()
    voices_dir.mkdir(parents=True, exist_ok=True)
    model = voices_dir / KOKORO_MODEL_FILE
    voices = voices_dir / KOKORO_VOICES_FILE
    targets = [(model, f"{_KOKORO_RELEASE}/{KOKORO_MODEL_FILE}"), (voices, f"{_KOKORO_RELEASE}/{KOKORO_VOICES_FILE}")]
    if all(_present(path) for path, _ in targets):
        return model, voices
    if client is None:
        import httpx

        client = httpx.Client(timeout=300.0, follow_redirects=True)
    for path, url in targets:
        if _present(path):
            continue
        write(f"Downloading {path.name} from GitHub...")
        try:
            resp = client.get(url)
            resp.raise_for_status()
        except Exception as exc:
            raise RuntimeError(f"Failed to download Kokoro model from {url}: {exc}") from exc
        path.write_bytes(resp.content)
    return model, voices


class PiperTTS:
    """Piper synthesis. The voice is lazy-loaded (or injected for tests)."""

    def __init__(self, voice_path, *, _voice=None) -> None:
        self._voice_path = str(voice_path)
        self._voice = _voice

    def _ensure(self):
        if self._voice is None:
            from piper.voice import PiperVoice

            self._voice = PiperVoice.load(self._voice_path)
        return self._voice

    @property
    def samplerate(self) -> int:
        return self._ensure().config.sample_rate

    def synth(self, text: str) -> bytes:
        # piper-tts >=1.3: synthesize() yields AudioChunk objects carrying raw
        # 16-bit PCM in `audio_int16_bytes` (older synthesize_stream_raw is gone).
        return b"".join(chunk.audio_int16_bytes for chunk in self._ensure().synthesize(text))


class KokoroTTS:
    """Kokoro-82M synthesis. The model is lazy-loaded (or injected for tests)."""

    SAMPLE_RATE = 24000

    def __init__(
        self, model_path, voices_path, *, voice: str = "bm_lewis", lang: str = "en-gb", _kokoro=None
    ) -> None:
        self._model_path = str(model_path)
        self._voices_path = str(voices_path)
        self._voice = voice
        self._lang = lang
        self._kokoro = _kokoro

    def _ensure(self):
        if self._kokoro is None:
            from kokoro_onnx import Kokoro

            self._kokoro = Kokoro(self._model_path, self._voices_path)
        return self._kokoro

    @property
    def samplerate(self) -> int:
        return self.SAMPLE_RATE

    def synth(self, text: str) -> bytes:
        import numpy as np

        samples, _sr = self._ensure().create(text, voice=self._voice, speed=1.0, lang=self._lang)
        # Kokoro returns float32 in [-1, 1]; convert to 16-bit mono PCM bytes.
        return (np.clip(samples, -1.0, 1.0) * 32767).astype("<i2").tobytes()


class SpeechPipeline:
    """Two-stage pipeline: a synth thread runs *ahead* of a play thread, so sentence
    N+1 is synthesized while N is still playing (gapless streaming, low time-to-first-audio).
    Both stages preserve order."""

    def __init__(self, tts, play, *, samplerate=None) -> None:
        self._tts = tts
        self._play = play
        self._samplerate = samplerate
        self._synth_q: queue.Queue = queue.Queue()
        self._play_q: queue.Queue = queue.Queue()
        self._stopped = threading.Event()
        self._synth_thread = threading.Thread(target=self._synth_worker, daemon=True)
        self._play_thread = threading.Thread(target=self._play_worker, daemon=True)
        self._synth_thread.start()
        self._play_thread.start()

    def _synth_worker(self) -> None:
        while True:
            item = self._synth_q.get()
            if item is None:
                self._play_q.put(None)  # forward shutdown to the play thread
                self._synth_q.task_done()
                return
            if not self._stopped.is_set():
                try:
                    pcm = self._tts.synth(item)
                    rate = self._samplerate or getattr(self._tts, "samplerate", 22050)
                    self._play_q.put((pcm, rate))
                except Exception as exc:
                    # Keep the pipeline alive on one bad sentence, but stay visible.
                    print(f"[voice] couldn't speak {item!r}: {exc}", file=sys.stderr)
            self._synth_q.task_done()

    def _play_worker(self) -> None:
        while True:
            item = self._play_q.get()
            if item is None:
                self._play_q.task_done()
                return
            if not self._stopped.is_set():
                pcm, rate = item
                try:
                    self._play(pcm, rate)
                except Exception as exc:
                    print(f"[voice] couldn't play audio: {exc}", file=sys.stderr)
            self._play_q.task_done()

    def say(self, sentence: str) -> None:
        if sentence and not self._stopped.is_set():
            self._synth_q.put(sentence)

    def drain(self) -> None:
        self._synth_q.join()  # everything synthesized + handed to the play queue
        self._play_q.join()  # everything played

    def clear(self) -> None:
        """Drop pending work on both stages without latching the pipeline off (e.g.
        abandon a half-spoken reply after an error, then carry on with the next turn)."""
        for q in (self._synth_q, self._play_q):
            try:
                while True:
                    q.get_nowait()
                    q.task_done()
            except queue.Empty:
                pass

    def stop(self) -> None:
        # Latching stop for shutdown: drop pending work and refuse further say().
        self._stopped.set()
        self.clear()

    def close(self) -> None:
        self._synth_q.put(None)
        self._synth_thread.join(timeout=2.0)
        self._play_thread.join(timeout=2.0)
