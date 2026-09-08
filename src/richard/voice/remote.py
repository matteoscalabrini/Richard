from __future__ import annotations

import io
import wave

import httpx


class RemoteTTS:
    """TTS via an OpenAI-compatible /v1/audio/speech endpoint (Chatterbox server or vLLM-Omni/Qwen3-TTS).

    Implements the same interface as PiperTTS/KokoroTTS (`synth` + `samplerate`) so it drops
    straight into SpeechPipeline. Needs only httpx — no heavy model deps on the client.
    """

    def __init__(
        self,
        endpoint: str,
        voice: str,
        *,
        model: str = "chatterbox",
        language: str | None = None,
        instructions: str | None = None,
        exaggeration: float | None = None,
        cfg_weight: float | None = None,
        temperature: float | None = None,
        speed_factor: float | None = None,
        client: httpx.Client | None = None,
        timeout: float = 60.0,
    ) -> None:
        self._url = endpoint.rstrip("/") + "/v1/audio/speech"
        self._voice = voice
        self._model = model
        self._language = language
        self._instructions = instructions
        # Chatterbox generation knobs; only sent when set, else the server's defaults apply.
        self._tuning = {
            "exaggeration": exaggeration,
            "cfg_weight": cfg_weight,
            "temperature": temperature,
            "speed_factor": speed_factor,
        }
        self._client = client or httpx.Client(timeout=timeout)
        self._samplerate = 24000

    @property
    def samplerate(self) -> int:
        return self._samplerate

    def synth(self, text: str) -> bytes:
        payload = {"input": text, "voice": self._voice, "response_format": "wav"}
        # Blank model = omit the field: vLLM-Omni serves one checkpoint and 404s on unknown names.
        if self._model:
            payload["model"] = self._model
        if self._language:
            payload["language"] = self._language
        if self._instructions:
            payload["instructions"] = self._instructions
        payload.update({k: v for k, v in self._tuning.items() if v is not None})
        response = self._client.post(self._url, json=payload)
        response.raise_for_status()
        with wave.open(io.BytesIO(response.content), "rb") as w:
            self._samplerate = w.getframerate()
            return w.readframes(w.getnframes())


class RemoteSTT:
    """STT via an OpenAI-compatible /v1/audio/transcriptions endpoint (Whisper on the GPU host)."""

    def __init__(
        self,
        endpoint: str,
        *,
        model: str = "Systran/faster-whisper-large-v3",
        client: httpx.Client | None = None,
        timeout: float = 60.0,
    ) -> None:
        self._url = endpoint.rstrip("/") + "/v1/audio/transcriptions"
        self._model = model
        self._client = client or httpx.Client(timeout=timeout)

    def transcribe(self, pcm: bytes, samplerate: int = 16000) -> str:
        if not pcm:
            return ""
        wav = io.BytesIO()
        with wave.open(wav, "wb") as w:
            w.setnchannels(1)
            w.setsampwidth(2)
            w.setframerate(samplerate)
            w.writeframes(pcm)
        response = self._client.post(
            self._url,
            files={"file": ("audio.wav", wav.getvalue(), "audio/wav")},
            data={"model": self._model},
        )
        response.raise_for_status()
        return (response.json().get("text") or "").strip()

    def transcribe_file(self, data: bytes, filename: str = "audio.webm") -> str:
        """Transcribe an already-encoded audio file (e.g. a browser MediaRecorder blob).

        faster-whisper decodes via ffmpeg, so webm/opus/mp3/wav all work regardless of suffix.
        """
        if not data:
            return ""
        response = self._client.post(
            self._url,
            files={"file": (filename, data, "application/octet-stream")},
            data={"model": self._model},
        )
        response.raise_for_status()
        return (response.json().get("text") or "").strip()
