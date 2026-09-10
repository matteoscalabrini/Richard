"""Frame sources and the hub that holds the latest frame of each.

A source only has to answer "what is the latest frame" and "give me your native
JPEG". The browser pushes; the robot (spec four's body plugin) pulls from the SDK;
a directory replays files for tests. Nothing here runs a thread: the pipeline
(pipeline.py) polls the hub at the stream rate.
"""
from __future__ import annotations

import re
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

import numpy as np

from richard.perception import image

SOURCE_ID_MAX_LENGTH = 64
_SOURCE_ID_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,63}\Z")


def validate_source_id(value, *, default: str | None = None) -> str:
    """Return a safe routing identifier, or raise without echoing hostile input."""
    if value is None and default is not None:
        value = default
    if not isinstance(value, str) or not _SOURCE_ID_RE.fullmatch(value):
        raise ValueError(
            f"source_id must be 1-{SOURCE_ID_MAX_LENGTH} letters, digits, '.', '_', ':', or '-'"
        )
    return value


@dataclass(frozen=True)
class Frame:
    ts: float  # monotonic seconds
    rgb: np.ndarray  # HxWx3 uint8
    source_id: str


class FrameSource(Protocol):
    source_id: str

    def latest(self) -> Frame | None: ...

    def native_jpeg(self) -> bytes | None: ...


class PushFrameSource:
    """Frames arrive as JPEG bytes from outside (the browser's POST). Thread-safe."""

    def __init__(self, source_id: str, *, clock=time.monotonic) -> None:
        self.source_id = source_id
        self._clock = clock
        self._lock = threading.Lock()
        self._frame: Frame | None = None
        self._jpeg: bytes | None = None

    def push_jpeg(self, data: bytes) -> Frame:
        try:
            rgb = image.decode(data)
        except Exception as exc:
            raise ValueError(f"frame is not a decodable image: {exc}") from exc
        frame = Frame(ts=self._clock(), rgb=rgb, source_id=self.source_id)
        with self._lock:
            self._frame, self._jpeg = frame, data
        return frame

    def latest(self) -> Frame | None:
        with self._lock:
            return self._frame

    def native_jpeg(self) -> bytes | None:
        with self._lock:
            return self._jpeg


class DirectoryFrameSource:
    """Replays image files from a directory in sorted order; tests and offline runs."""

    def __init__(self, source_id: str, directory, *, fps: float = 2.0, loop: bool = False,
                 clock=time.monotonic) -> None:
        self.source_id = source_id
        self._paths = sorted(p for p in Path(directory).iterdir() if p.suffix.lower() in (".jpg", ".jpeg", ".png"))
        self._index = 0
        self._loop = loop
        self._period = 1.0 / max(fps, 0.01)
        self._clock = clock
        self._frame: Frame | None = None
        self._jpeg: bytes | None = None

    def advance(self) -> Frame | None:
        if self._index >= len(self._paths):
            if not self._loop or not self._paths:
                return None
            self._index = 0
        data = self._paths[self._index].read_bytes()
        self._index += 1
        self._frame = Frame(ts=self._clock(), rgb=image.decode(data), source_id=self.source_id)
        self._jpeg = data
        return self._frame

    def latest(self) -> Frame | None:
        return self._frame

    def native_jpeg(self) -> bytes | None:
        return self._jpeg


class FrameHub:
    """The latest frame of every registered source, and whether it has gone quiet."""

    def __init__(self, *, stale_s: float = 5.0, clock=time.monotonic) -> None:
        self._stale_s = stale_s
        self._clock = clock
        self._lock = threading.Lock()
        self._sources: dict[str, FrameSource] = {}

    def add(self, source: FrameSource) -> None:
        with self._lock:
            self._sources[source.source_id] = source

    def remove(self, source_id: str) -> None:
        with self._lock:
            self._sources.pop(source_id, None)

    def get(self, source_id: str) -> FrameSource | None:
        with self._lock:
            return self._sources.get(source_id)

    def sources(self) -> list[str]:
        with self._lock:
            return list(self._sources)

    def latest(self, source_id: str) -> Frame | None:
        source = self.get(source_id)
        return source.latest() if source is not None else None

    def native_jpeg(self, source_id: str) -> bytes | None:
        source = self.get(source_id)
        return source.native_jpeg() if source is not None else None

    def is_stale(self, source_id: str) -> bool:
        frame = self.latest(source_id)
        return frame is None or (self._clock() - frame.ts) > self._stale_s
