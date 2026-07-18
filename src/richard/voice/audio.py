from __future__ import annotations

from collections.abc import Iterator


def record_stream(
    samplerate: int = 16000, frame_ms: int = 20, *, device=None, _stream_factory=None
) -> Iterator[bytes]:
    """Yield fixed-size 16-bit mono PCM frames from the microphone until the generator is closed."""
    frames_per_chunk = int(samplerate * frame_ms / 1000)
    if _stream_factory is None:
        import sounddevice as sd

        def _stream_factory():
            return sd.RawInputStream(
                samplerate=samplerate,
                channels=1,
                dtype="int16",
                blocksize=frames_per_chunk,
                device=device,
            )

    stream = _stream_factory()
    stream.start()
    try:
        while True:
            data, _overflowed = stream.read(frames_per_chunk)
            yield bytes(data)
    finally:
        stream.stop()
        stream.close()


def play(pcm: bytes, samplerate: int, *, device=None, _stream_factory=None) -> None:
    """Play 16-bit mono PCM through the speaker (blocking)."""
    if _stream_factory is None:
        import sounddevice as sd

        def _stream_factory():
            return sd.RawOutputStream(
                samplerate=samplerate, channels=1, dtype="int16", device=device
            )

    stream = _stream_factory()
    stream.start()
    try:
        stream.write(pcm)
    finally:
        stream.stop()
        stream.close()
