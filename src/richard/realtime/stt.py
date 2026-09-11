"""Turn transcription for realtime sessions: partial passes while the user is
still speaking (live captions, warm decode) and a final pass on endpoint.

One instance is shared across sessions — the model lives once in VRAM and a lock
serializes decodes (ctranslate2 handles concurrency, but serialized decodes keep
latency predictable and memory bounded). Partial == final implementation-wise:
on a GPU a few-second utterance decodes in ~100–300 ms, so re-decoding the whole
buffer each pass is simpler and fast enough (spec's chosen approach).
"""
from __future__ import annotations

import threading
from dataclasses import dataclass

import numpy as np


@dataclass(frozen=True)
class Transcription:
    text: str
    language: str | None


class TurnTranscriber:
    def __init__(self, model: str = "large-v3-turbo", *, device: str = "auto",
                 compute_type: str = "default", language: str | None = None,
                 _model=None) -> None:
        self._model_name = model
        self._device = device
        self._compute_type = compute_type
        self._language = language
        self._model = _model
        self._lock = threading.Lock()

    def _ensure(self):
        if self._model is None:
            from faster_whisper import WhisperModel

            self._model = WhisperModel(
                self._model_name, device=self._device, compute_type=self._compute_type
            )
        return self._model

    def _decode(self, pcm: bytes) -> Transcription:
        if not pcm:
            return Transcription("", None)
        audio = np.frombuffer(pcm, dtype=np.int16).astype(np.float32) / 32768.0
        with self._lock:
            segments, info = self._ensure().transcribe(
                audio,
                beam_size=1,
                language=self._language,
                vad_filter=False,  # endpointing already ran Silero; don't double-gate
                condition_on_previous_text=False,
            )
            language = getattr(info, "language", None) or self._language
            return Transcription("".join(s.text for s in segments).strip(), language)

    def partial(self, pcm: bytes) -> str:
        return self._decode(pcm).text

    def final(self, pcm: bytes) -> str:
        return self._decode(pcm).text

    def final_with_language(self, pcm: bytes) -> Transcription:
        return self._decode(pcm)
