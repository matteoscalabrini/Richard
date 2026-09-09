"""One pipeline per frame source, one service for all of them.

SourcePipeline.step(): pull the latest frame (skip if unchanged), motion stage,
person stage, face stage (opt-in, only while someone unnamed is present, once a
second), presence state → raw events. PerceptionService: hub, pipelines, gate,
log, gallery, sinks; the thread per source runs step() at the stream rate. Nothing
here touches a socket; the web app and the plugin call into it.
"""
from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from richard.perception import image
from richard.perception.events import PerceptionEvent, PresenceLog, PresenceState
from richard.perception.frames import FrameHub, PushFrameSource
from richard.perception.gate import Gate, GatePolicy
from richard.perception.motion import MotionDetector, StillnessTracker
from richard.plugins.base import Event

log = logging.getLogger("richard.perception")


@dataclass
class Settings:
    identity_enabled: bool = False
    quiet_hours: str = ""
    sensitivity: int = 50
    cooldown_s: float = 120.0
    enter_debounce_s: float = 2.0
    leave_debounce_s: float = 10.0
    stream_fps: float = 2.0
    keep_thumbnails: bool = False
    device: str = "cpu"
    stale_s: float = 5.0

    @classmethod
    def from_table(cls, table: dict) -> "Settings":
        s = cls()
        for key in cls.__dataclass_fields__:
            if key in table and table[key] is not None:
                current = getattr(s, key)
                value = table[key]
                setattr(s, key, bool(value) if isinstance(current, bool) else type(current)(value))
        s.sensitivity = min(100, max(0, int(s.sensitivity)))
        return s

    @property
    def motion_threshold(self) -> float:
        return 0.02 + (100 - self.sensitivity) / 100 * 0.08

    @property
    def person_threshold(self) -> float:
        return 0.4 + (100 - self.sensitivity) / 100 * 0.3


class SourcePipeline:
    def __init__(self, source_id: str, hub: FrameHub, *, settings: Settings, person_detector=None,
                 identifier=None, clock=time.monotonic, face_every_s: float = 1.0) -> None:
        self.source_id = source_id
        self._hub = hub
        self._settings = settings
        self._person_detector = person_detector
        self._identifier = identifier
        self._clock = clock
        self._face_every = face_every_s
        self._motion = MotionDetector(threshold=settings.motion_threshold)
        self._stillness = StillnessTracker()
        self.presence = PresenceState(source_id, enter_debounce_s=settings.enter_debounce_s,
                                      leave_debounce_s=settings.leave_debounce_s)
        self._last_ts: float | None = None
        self._last_face_at: float = -1e9
        self.frames = 0

    def step(self) -> list[PerceptionEvent]:
        frame = self._hub.latest(self.source_id)
        if frame is None or frame.ts == self._last_ts:
            return []
        self._last_ts = frame.ts
        self.frames += 1
        ts = frame.ts
        events: list[PerceptionEvent] = []
        reading = self._motion.feed(image.grey_small(frame.rgb), ts)
        if reading.scene_changed:
            events.append(PerceptionEvent(ts, self.source_id, "scene_changed"))
        still_since = self._stillness.still_since
        minutes = self._stillness.feed(reading.activity, self._settings.motion_threshold, ts)
        if minutes is not None:
            events.append(PerceptionEvent(ts, self.source_id, "stillness", str(minutes)))
        if reading.activity > self._settings.motion_threshold and still_since is not None and ts - still_since >= 120.0:
            events.append(PerceptionEvent(ts, self.source_id, "motion_after_stillness", confidence=reading.activity))
        persons = []
        if self._person_detector is not None:
            try:
                persons = [d for d in self._person_detector.detect(frame.rgb) if d.score >= self._settings.person_threshold]
            except Exception as exc:  # a detector fault must not kill the pipeline
                log.warning("perception: person detector failed on %s: %s", self.source_id, exc)
        names: list[str | None] = [None] * len(persons)
        present = self.presence.present()
        unresolved = bool(persons) and (not present or present[0].subject == "unknown")
        if self._identifier is not None and self._settings.identity_enabled and unresolved and ts - self._last_face_at >= self._face_every:
            self._last_face_at = ts
            try:
                matches = self._identifier.identify(frame.rgb)
                names = [m.name for m in matches if m.name] or names
            except Exception as exc:
                log.warning("perception: face identification failed on %s: %s", self.source_id, exc)
        events.extend(self.presence.observe(ts, persons, names))
        return events


class PerceptionService:
    def __init__(self, settings: Settings, *, data_dir: Path | str, clock=time.monotonic, wall=datetime.now,
                 person_detector=None, identifier=None, write=print) -> None:
        self.settings = settings
        self._data_dir = Path(data_dir)
        self._clock = clock
        self._write = write
        self._person_detector = person_detector
        self._identifier = identifier
        self.hub = FrameHub(stale_s=settings.stale_s, clock=clock)
        self.gate = Gate(GatePolicy(enabled=True, quiet_hours=settings.quiet_hours, cooldown_s=settings.cooldown_s),
                         clock=clock, wall=wall)
        self.log = PresenceLog(self._data_dir / "perception.db")
        self.gallery = None
        if settings.identity_enabled:
            from richard.perception.faces import Gallery

            self.gallery = Gallery(self._data_dir / "faces.db")
        self._pipelines: dict[str, SourcePipeline] = {}
        self._sinks: list = []
        self._context_sink = None
        self._lock = threading.Lock()
        self._threads: dict[str, threading.Thread] = {}
        self._stop = threading.Event()
        self._frames_received: dict[str, int] = {}
        self.started = False

    # -- sources ------------------------------------------------------------

    def _ensure_source(self, source_id: str) -> PushFrameSource:
        with self._lock:
            source = self.hub.get(source_id)
            if source is None:
                source = PushFrameSource(source_id, clock=self._clock)
                self.hub.add(source)
                self._pipelines[source_id] = SourcePipeline(
                    source_id, self.hub, settings=self.settings, person_detector=self._person_detector,
                    identifier=self._identifier, clock=self._clock)
                if self.started:
                    self._spawn(source_id)
            return source

    def push_frame(self, source_id: str, jpeg: bytes) -> None:
        self._ensure_source(source_id).push_jpeg(jpeg)
        self._frames_received[source_id] = self._frames_received.get(source_id, 0) + 1

    def remove_source(self, source_id: str) -> None:
        with self._lock:
            self.hub.remove(source_id)
            self._pipelines.pop(source_id, None)

    def live_sources(self) -> list[str]:
        return [s for s in self.hub.sources() if not self.hub.is_stale(s)]

    # -- run ----------------------------------------------------------------

    def _spawn(self, source_id: str) -> None:
        period = 1.0 / max(self.settings.stream_fps, 0.2)

        def run() -> None:
            while not self._stop.is_set():
                try:
                    self.process(source_id)
                except Exception as exc:
                    log.warning("perception: pipeline %s failed: %s", source_id, exc)
                self._stop.wait(period)

        thread = threading.Thread(target=run, name=f"richard-perception-{source_id}", daemon=True)
        self._threads[source_id] = thread
        thread.start()

    def start(self) -> None:
        self._stop.clear()
        self.started = True
        for source_id in list(self._pipelines):
            self._spawn(source_id)

    def stop(self) -> None:
        self._stop.set()
        self.started = False
        for thread in self._threads.values():
            thread.join(timeout=2.0)
        self._threads.clear()

    # -- events ---------------------------------------------------------------

    def add_sink(self, fn) -> None:
        self._sinks.append(fn)

    def set_context_sink(self, fn) -> None:
        self._context_sink = fn

    @staticmethod
    def _target(event: PerceptionEvent) -> str:
        if event.kind in ("person_entered", "person_left"):
            return "person_present"
        if event.kind == "identified":
            return f"identified:{event.subject}"
        return event.kind

    def event_source(self, sink):
        """Plugin contract: start(sink) -> stop(). Events become richard.plugins.base.Event."""
        def forward(event: PerceptionEvent) -> None:
            sink(Event(kind=event.kind, target=f"perception:{self._target(event)}", payload=event.to_dict(),
                       observed_at=datetime.now(timezone.utc)))

        self._sinks.append(forward)

        def stop() -> None:
            if forward in self._sinks:
                self._sinks.remove(forward)
        return stop

    def process(self, source_id: str) -> list[PerceptionEvent]:
        pipeline = self._pipelines.get(source_id)
        if pipeline is None:
            return []
        raw = pipeline.step()
        admitted = self.gate.admit(raw)
        for reason in self.gate.reasons:
            log.info("perception: dropped %s (%s)", reason[0], reason[1])
        for event in admitted:
            thumbnail = None
            if self.settings.keep_thumbnails:
                frame = self.hub.latest(source_id)
                if frame is not None:
                    thumbnail = image.encode_jpeg(image.resize_long_edge(frame.rgb, 320), quality=70)
            self.log.append(event, thumbnail)
            log.info("perception: %s", event.line())
            taken = False
            if self._context_sink is not None:
                try:
                    taken = bool(self._context_sink(event.line()))
                except Exception as exc:
                    log.warning("perception: context sink failed: %s", exc)
            if not taken:
                for sink in list(self._sinks):
                    try:
                        sink(event)
                    except Exception as exc:
                        log.warning("perception: sink failed: %s", exc)
        return admitted

    # -- queries ----------------------------------------------------------------

    def status(self) -> dict:
        sources = [{"id": s, "stale": self.hub.is_stale(s), "frames": self._frames_received.get(s, 0)}
                   for s in self.hub.sources() if s in self._pipelines]
        return {"enabled": True, "identity_enabled": self.settings.identity_enabled, "sources": sources,
                "presence": self.presence(), "gallery": self.gallery.list() if self.gallery else []}

    def presence(self) -> list[dict]:
        out = []
        for source_id, pipeline in self._pipelines.items():
            for person in pipeline.presence.present():
                out.append({"source": source_id, "subject": person.subject, "since": person.since})
        return out

    def recent_events(self, since_id: int = 0, limit: int = 100) -> list[dict]:
        return self.log.recent(since_id, limit)

    def snapshot(self, source_id: str | None = None, detail: str = "low", region: str | None = None):
        """(jpeg, meta) from the latest frame of a live source, or None."""
        candidates = [source_id] if source_id else self.live_sources()
        for sid in candidates:
            frame = self.hub.latest(sid)
            if frame is None or self.hub.is_stale(sid):
                continue
            rgb = frame.rgb
            meta = {"source": sid, "detail": detail, "region": region}
            if region:
                rgb = image.crop(rgb, image.region_box(region))
                rgb = image.resize_long_edge(rgb, 800)
            elif detail != "high":
                rgb = image.resize_long_edge(rgb, 800)
            meta["width"], meta["height"] = int(rgb.shape[1]), int(rgb.shape[0])
            return image.encode_jpeg(rgb, quality=85), meta
        return None

    def enrol(self, name: str, jpegs: list[bytes]) -> int:
        if self.gallery is None or self._identifier is None:
            raise ValueError("identity is not enabled")
        vectors = []
        for data in jpegs:
            rgb = image.decode(data)
            faces = self._identifier.identify(rgb)
            if len(faces) != 1:
                raise ValueError(f"expected exactly one face per snapshot, found {len(faces)}")
            vectors.append(self._identifier.embed_box(rgb, faces[0].box))
        return self.gallery.enrol(name, vectors)
