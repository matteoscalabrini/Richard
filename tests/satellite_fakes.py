from __future__ import annotations

from collections.abc import Iterator


class FakeRelayConnection:
    """In-memory RelayConnection for tests. Scripts inbound audio and records
    everything sent."""

    def __init__(self, *, inbound_audio: list[bytes] | None = None) -> None:
        self._inbound_audio = list(inbound_audio or [])
        self.sent: list = []            # control messages sent (protocol dataclasses)
        self.sent_audio: list[bytes] = []

    def send(self, msg) -> None:
        self.sent.append(msg)

    def send_audio(self, frame: bytes) -> None:
        self.sent_audio.append(frame)

    def audio_frames(self) -> Iterator[bytes]:
        yield from self._inbound_audio
