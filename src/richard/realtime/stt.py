"""Turn transcription for realtime sessions: partial passes while the user is
still speaking (live captions, warm decode) and a final pass on endpoint.

One instance is shared across sessions — the model lives once in VRAM and a lock
serializes decodes (ctranslate2 handles concurrency, but serialized decodes keep
latency predictable and memory bounded). Partial == final implementation-wise:
on a GPU a few-second utterance decodes in ~100–300 ms, so re-decoding the whole
buffer each pass is simpler and fast enough (spec's chosen approach).
"""
from __future__ import annotations

import logging
import threading
from dataclasses import dataclass

import numpy as np

logger = logging.getLogger("richard.realtime")


@dataclass(frozen=True)
class Transcription:
    text: str
    language: str | None


class TurnTranscriber:
    def __init__(self, model: str = "large-v3-turbo", *, device: str = "auto",
                 compute_type: str = "default", language: str | None = None,
                 languages: tuple[str, ...] = ("it", "en"),
                 _model=None) -> None:
        self._model_name = model
        self._device = device
        self._compute_type = compute_type
        self._language = language
        self._languages = tuple(languages)
        self._model = _model
        self._lock = threading.Lock()
        self._last_language: str | None = None

    def _ensure(self):
        if self._model is None:
            from faster_whisper import WhisperModel

            self._model = WhisperModel(
                self._model_name, device=self._device, compute_type=self._compute_type
            )
        return self._model

    def _detect_language(self, model, audio) -> str | None:
        """The language to force on transcribe(): the configured hint, or — when
        auto-detecting among a restricted set — the allowed language with the
        highest probability from detect_language(), falling back to the first
        allowed language if detection is unavailable or errors out.

        Only called on a final pass (once per utterance) — detect_language is a
        model call of its own and partials must stay cheap; they reuse whatever
        the most recent final detected via self._last_language."""
        if self._language is not None or not self._languages:
            return self._language
        try:
            _, _, all_probs = model.detect_language(audio)
        except Exception:
            return self._languages[0]
        probs = {code: prob for code, prob in all_probs}
        allowed = [(code, probs.get(code, 0.0)) for code in self._languages]
        return max(allowed, key=lambda cp: cp[1])[0]

    def _partial_language(self) -> str | None:
        if self._language is not None:
            return self._language
        if self._last_language is not None:
            return self._last_language
        if self._languages:
            return self._languages[0]
        return None

    def _decode(self, pcm: bytes, *, detect: bool) -> Transcription:
        if not pcm:
            return Transcription("", None)
        audio = np.frombuffer(pcm, dtype=np.int16).astype(np.float32) / 32768.0
        with self._lock:
            model = self._ensure()
            language = self._detect_language(model, audio) if detect else self._partial_language()
            segments, info = model.transcribe(
                audio,
                beam_size=1,
                language=language,
                vad_filter=False,  # endpointing already ran Silero; don't double-gate
                condition_on_previous_text=False,
            )
            kept = []
            for s in segments:
                if getattr(s, "no_speech_prob", 0.0) > 0.6:
                    continue
                avg_logprob = getattr(s, "avg_logprob", 0.0)
                if avg_logprob < -1.0:
                    logger.debug(
                        "stt: low avg_logprob=%.2f kept, text_len=%d", avg_logprob, len(s.text)
                    )
                kept.append(s)
            language = getattr(info, "language", None) or language
            if detect:
                self._last_language = language
            return Transcription("".join(s.text for s in kept).strip(), language)

    def partial(self, pcm: bytes) -> str:
        return self._decode(pcm, detect=False).text

    def final(self, pcm: bytes) -> str:
        return self._decode(pcm, detect=True).text

    def final_with_language(self, pcm: bytes) -> Transcription:
        return self._decode(pcm, detect=True)
