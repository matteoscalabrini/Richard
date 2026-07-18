from __future__ import annotations

from collections.abc import Iterator
from typing import Protocol

from richard.satellite.protocol import ControlMessage


class RelayConnection(Protocol):
    """The transport seam between a satellite (hub/app) and the SatelliteManager.

    A control message is one of the dataclasses in protocol.py. Audio is raw 16-bit
    mono PCM. Implementations: the real websockets adapter (server.py) and the
    in-memory FakeRelayConnection (tests).
    """

    def send(self, msg: ControlMessage) -> None:
        """Send a control message (encode + transmit)."""
        ...

    def send_audio(self, frame: bytes) -> None:
        """Send a downlink (TTS) audio frame."""
        ...

    def audio_frames(self) -> Iterator[bytes]:
        """Yield inbound (mic) PCM frames for the current utterance, in arrival order."""
        ...
