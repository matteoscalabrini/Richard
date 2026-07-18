from __future__ import annotations

import io
import wave


class WhisperSTT:
    """faster-whisper wrapper. The model is lazy-loaded (or injected for tests)."""

    def __init__(
        self,
        model: str = "base.en",
        *,
        compute_type: str = "int8",
        vad_filter: bool = True,
        _model=None,
    ) -> None:
        self._model_name = model
        self._compute_type = compute_type
        self._vad_filter = vad_filter
        self._model = _model

    def _ensure(self):
        if self._model is None:
            from faster_whisper import WhisperModel

            self._model = WhisperModel(
                self._model_name, device="cpu", compute_type=self._compute_type
            )
        return self._model

    def transcribe(self, pcm: bytes, samplerate: int = 16000) -> str:
        if not pcm:
            return ""
        wav = io.BytesIO()
        with wave.open(wav, "wb") as w:
            w.setnchannels(1)
            w.setsampwidth(2)
            w.setframerate(samplerate)
            w.writeframes(pcm)
        wav.seek(0)
        # vad_filter runs faster-whisper's bundled Silero VAD to strip non-speech
        # before decoding (kills most silence-driven hallucinations); not conditioning
        # on previous text stops fabricated repeat-loops.
        segments, _info = self._ensure().transcribe(
            wav,
            beam_size=1,
            vad_filter=self._vad_filter,
            condition_on_previous_text=False,
        )
        return "".join(seg.text for seg in segments).strip()
