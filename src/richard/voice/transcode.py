"""Normalize uploaded reference samples before they reach the TTS server.

The Qwen3-TTS server accepts WAV/MP3/FLAC/OGG between 1s and 30s and returns a
plain 400 for anything else (m4a, webm, too-long clips, ...). Richard forwarded
files unchanged, so anything outside that window always failed. This transcodes
with ffmpeg (when available) to a 24kHz mono 16-bit WAV, capped at `max_seconds`,
before the upload — closing the biggest source of "upload failed" reports.
"""
from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

from richard.voice.voices import VoiceUploadError

_SAMPLE_RATE = 24000
_SAMPLE_WIDTH = 2  # bytes, s16
_WAV_HEADER_BYTES = 44


def to_reference_wav(
    audio: bytes,
    filename: str,
    *,
    ffmpeg: str | None = None,
    max_seconds: float = 30.0,
) -> tuple[bytes, str]:
    """Transcode `audio` to a 24kHz mono WAV, capped at `max_seconds`.

    Returns the (possibly unchanged) bytes and filename. With no ffmpeg on PATH
    (and none given), returns `audio`/`filename` unchanged — today's behaviour.
    """
    binary = ffmpeg if ffmpeg is not None else shutil.which("ffmpeg")
    if not binary:
        return audio, filename

    try:
        result = subprocess.run(
            [
                binary,
                "-hide_banner", "-loglevel", "error",
                "-i", "pipe:0",
                "-t", str(max_seconds),
                "-ac", "1",
                "-ar", str(_SAMPLE_RATE),
                "-sample_fmt", "s16",
                "-f", "wav",
                "pipe:1",
            ],
            input=audio,
            capture_output=True,
            timeout=60,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise VoiceUploadError(f"could not decode {filename}: {exc}") from exc

    if result.returncode != 0:
        stderr_tail = result.stderr.decode("utf-8", errors="replace")[-200:].strip()
        raise VoiceUploadError(f"could not decode {filename}: {stderr_tail}")

    wav_bytes = result.stdout
    duration = max(0, len(wav_bytes) - _WAV_HEADER_BYTES) / (_SAMPLE_RATE * _SAMPLE_WIDTH)
    if duration < 1.0:
        raise VoiceUploadError(
            f"reference audio too short ({duration:.1f} s); at least 1 s of clear speech is required"
        )

    return wav_bytes, Path(filename).stem + ".wav"
