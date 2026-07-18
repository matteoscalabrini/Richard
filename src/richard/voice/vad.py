from __future__ import annotations

from collections.abc import Iterable


def record_utterance(
    frames: Iterable[bytes],
    samplerate: int = 16000,
    *,
    frame_ms: int = 20,
    aggressiveness: int = 2,
    silence_ms: int = 800,
    max_ms: int = 15000,
    vad=None,
) -> bytes:
    """Consume PCM frames; return the utterance once trailing silence (or max length) is reached."""
    if vad is None:
        import webrtcvad

        vad = webrtcvad.Vad(aggressiveness)
    silence_limit = max(1, silence_ms // frame_ms)
    max_frames = max(1, max_ms // frame_ms)
    collected = bytearray()
    started = False
    trailing_silence = 0
    frame_count = 0
    for frame in frames:
        frame_count += 1
        try:
            speech = vad.is_speech(frame, samplerate)
        except ValueError:
            # webrtcvad rejects frames that aren't 10/20/30 ms at 8/16/32/48 kHz.
            # That's a configuration error (frame_ms vs capture block size, or an
            # unsupported samplerate) — surface it instead of returning silence.
            raise
        except Exception:
            speech = False
        if speech:
            started = True
            trailing_silence = 0
            collected += frame
        elif started:
            trailing_silence += 1
            collected += frame
            if trailing_silence >= silence_limit:
                break
        if frame_count >= max_frames:
            break
    return bytes(collected)
