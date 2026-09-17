"""The TTS server's voice library: vLLM-Omni / Qwen3-TTS `/v1/audio/voices`.

Upload once, the server keeps the sample and its speaker embedding across restarts.
The file is forwarded unchanged; the server resamples.
"""
from __future__ import annotations

import mimetypes
from pathlib import Path

import httpx

# Pinned so uploads carry the same type from every platform (macOS guesses audio/x-wav).
_MIME_BY_SUFFIX = {".wav": "audio/wav", ".mp3": "audio/mpeg", ".flac": "audio/flac", ".ogg": "audio/ogg", ".m4a": "audio/mp4"}


class VoiceUploadError(RuntimeError):
    """Raised when the TTS server rejects an uploaded reference sample.

    Carries the server's own explanation (bad format, too short/long, ...) so the
    caller can show it instead of a generic "400 Bad Request".
    """


def _server_error_message(response: httpx.Response) -> str:
    try:
        data = response.json()
    except ValueError:
        return response.text.strip() or response.reason_phrase
    if isinstance(data, dict):
        error = data.get("error")
        if isinstance(error, dict) and error.get("message"):
            return str(error["message"])
    return response.text.strip() or response.reason_phrase


class VoiceLibrary:
    def __init__(self, endpoint: str, *, client: httpx.Client | None = None, timeout: float = 120.0) -> None:
        self._url = endpoint.rstrip("/") + "/v1/audio/voices"
        self._client = client or httpx.Client(timeout=timeout)

    def list(self) -> dict:
        response = self._client.get(self._url)
        response.raise_for_status()
        data = response.json()
        uploaded = [
            {
                "name": str(item.get("name", "")),
                "ref_text": str(item.get("ref_text") or ""),
                "created_at": item.get("created_at"),
            }
            for item in data.get("uploaded_voices", [])
            if isinstance(item, dict)
        ]
        return {"voices": [str(name) for name in data.get("voices", [])], "uploaded": uploaded}

    def upload(self, name: str, audio: bytes, filename: str, *, transcript: str = "", consent: str) -> dict:
        mime = _MIME_BY_SUFFIX.get(Path(filename).suffix.lower()) or mimetypes.guess_type(filename)[0] or "application/octet-stream"
        response = self._client.post(
            self._url,
            files={"audio_sample": (filename, audio, mime)},
            data={"name": name, "ref_text": transcript, "consent": consent},
        )
        try:
            response.raise_for_status()
        except httpx.HTTPStatusError as exc:
            raise VoiceUploadError(_server_error_message(exc.response)) from exc
        try:
            return response.json()
        except ValueError:
            return {}
