# Ambient perception (spec 3b): sensor, gate, brain — implementation plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Richard watches continuously at near-zero cost, notices people arriving and leaving (and who they are, opt-in), logs it, tells the brain only what passes a deterministic gate, and can look at the latest frame himself at the detail and region he asks for.

**Architecture:** A core package `richard.perception` (frames, sensor stages, events, gate, pipeline, log, camera providers) and a built-in plugin `richard.plugins.perception` that scopes the capability. The browser streams webcam frames to `POST /api/perception/frame`; a per-source pipeline thread runs motion → person → face stages and a presence state machine; the gate admits transitions with debounce, cooldowns and quiet hours; admitted events go to the presence log, to the control-loop monitor (target kind `perception`), and, when a realtime session is open, into that session as a `[perception]` context item before its next turn. The brain gets a server-side `camera(question, detail, region)` tool served from the latest frame; the engine learns tool results that carry images.

**Tech Stack:** Python 3.11+, numpy, Pillow + onnxruntime (new `perception` extra), sqlite3, pytest; ONNX models downloaded on first use: SSD-MobileNetV1 (`ssd_mobilenet_v1_12.onnx`, MIT, 28 MB), UltraFace (`version-RFB-320.onnx`, MIT, 1.3 MB), ArcFace int8 (`arcfaceresnet100-11-int8.onnx`, Apache 2.0, 66 MB); vanilla JS in `static.py`.

## Global Constraints

- Spec: `docs/superpowers/specs/2026-09-09-ambient-perception-design.md`. Read it once before starting. Spec 3a is built (HEAD dec155b) and its names are used as-is: `Message`, `user_parts`, `vision.check_image_data_url`, `Engine.respond_streaming(conversation, client_tools=)`, `ClientToolCall`, `RealtimeSession.create_item/create_response`.
- Work in the worktree `~/Documents/GitHub/Richard/.worktrees/reachy-presence`, branch `reachy-presence`. Tests: `.venv/bin/python -m pytest -q` from the worktree root (excludes `gpu` and `slow`). Baseline: 667 passed, 2 skipped, 3 deselected. First install the new extra into the dev venv: `uv pip install --python .venv/bin/python -e ".[dev,perception]"` (the venv has no pip).
- Commit messages end with `Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>`. Never push in this plan.
- The lean core stays numpy-only: every Pillow or onnxruntime import lives inside `richard.perception` (lazy, inside functions or classes), never at module top level of anything imported by `richard.cli` at startup. Tests that need Pillow use `pytest.importorskip("PIL")` at module top.
- Config lives under `[plugins.perception]` with these defaults, exact keys: `identity_enabled=false`, `quiet_hours=""` (e.g. `"23:00-07:30"`), `sensitivity=50` (0-100), `cooldown_s=120`, `enter_debounce_s=2`, `leave_debounce_s=10`, `stream_fps=2`, `keep_thumbnails=false`, `device="cpu"`, `stale_s=5`.
- Frame sizes: the browser streams 800 px long edge JPEG q0.7 at `stream_fps`; the sensor works on 320 px wide copies (person detector) and 160x90 grey (motion); low detail = 800 px, high = the source's native frame, region = crop at native then bounded to 800 px.
- Event kinds, exact strings: `person_entered`, `person_left`, `identified`, `unknown_person`, `motion_after_stillness`, `scene_changed`, `stillness`. Presence subjects: a gallery name or `unknown`.
- Privacy: no frame or embedding leaves the box; the gallery is opt-in (`identity_enabled`), enrolled only through the UI, deletable; the log stores no thumbnails unless `keep_thumbnails`.
- Models are stored under `richard.realtime.vad.default_models_dir()` (`~/.richard/models`), downloaded with `httpx` `follow_redirects=True` exactly like `ensure_silero`.

---

## File structure

| file | responsibility |
|---|---|
| `pyproject.toml` | `perception` extra (`Pillow>=10`, `onnxruntime>=1.17`), entry point `perception = richard.plugins.perception:PerceptionPlugin` |
| `src/richard/perception/__init__.py` | package doc only |
| `src/richard/perception/image.py` | Pillow-backed decode/encode/resize/crop/grey, region boxes, data URLs |
| `src/richard/perception/frames.py` | `Frame`, `FrameSource` protocol, `PushFrameSource`, `DirectoryFrameSource`, `FrameHub` |
| `src/richard/perception/motion.py` | `MotionDetector`, `StillnessTracker` (numpy only) |
| `src/richard/perception/models.py` | model registry + `ensure_model`, `make_session` (onnxruntime, thread count fixed) |
| `src/richard/perception/detect.py` | `PersonDetector` (SSD-MobileNetV1) |
| `src/richard/perception/faces.py` | `FaceDetector` (UltraFace + NMS), `FaceEmbedder` (ArcFace), `Gallery` (sqlite), `FaceIdentifier` |
| `src/richard/perception/events.py` | `PerceptionEvent`, `PresenceState`, `PresenceLog` (sqlite) |
| `src/richard/perception/gate.py` | `GatePolicy`, `Gate` |
| `src/richard/perception/pipeline.py` | `SourcePipeline` (thread per source), `PerceptionService` (hub + pipelines + gate + log + sinks + snapshots) |
| `src/richard/perception/camera.py` | `CameraProvider` (`camera`), `PresenceProvider` (`who_is_here`, `last_seen`) |
| `src/richard/perception/reader.py` | `PerceptionTargetReader` (kind `perception`) |
| `src/richard/plugins/perception/__init__.py` | `PerceptionPlugin` |
| `src/richard/providers/base.py`, `src/richard/engine.py` | `ToolResult` with images; engine appends the image as a user message after the tool message |
| `src/richard/realtime/registry.py`, `session.py`, `cli.py` | `SessionRegistry`, `RealtimeSession.add_context()`, wiring |
| `src/richard/web/app.py`, `static.py` | `/api/perception/*` routes; Perception drawer page; home-page streamer |
| `docs/perception.md`, `README.md` | docs |

---

### Task 1: `perception` extra and the image helpers

**Files:**
- Modify: `pyproject.toml`
- Create: `src/richard/perception/__init__.py`, `src/richard/perception/image.py`
- Test: `tests/test_perception_image.py`

**Interfaces:**
- Produces: `decode(data: bytes) -> np.ndarray` (HxWx3 RGB uint8); `encode_jpeg(rgb, quality=85) -> bytes`; `resize_long_edge(rgb, max_edge) -> np.ndarray`; `crop(rgb, box) -> np.ndarray` (box = normalised `(x0, y0, x1, y1)`); `grey_small(rgb, width=160, height=90) -> np.ndarray` float32; `region_box(region: str) -> tuple[float, float, float, float]` (raises `ValueError`); `data_url(jpeg: bytes) -> str`; `REGIONS`.

- [ ] **Step 1: Add the extra and install it**

In `pyproject.toml` under `[project.optional-dependencies]` add:

```toml
perception = [
    "Pillow>=10",
    "onnxruntime>=1.17",
]
```

and under `[project.entry-points."richard.plugins"]` add `perception = "richard.plugins.perception:PerceptionPlugin"` (the plugin module arrives in Task 12; the entry point is only loaded when the plugin is enabled).

Run: `uv pip install --python .venv/bin/python -e ".[dev,perception]"` and check `.venv/bin/python -c "import PIL, onnxruntime; print(PIL.__version__, onnxruntime.__version__)"`.

- [ ] **Step 2: Write the failing tests**

Create `tests/test_perception_image.py`:

```python
import io

import numpy as np
import pytest

PIL = pytest.importorskip("PIL")
from PIL import Image  # noqa: E402

from richard.perception import image as img  # noqa: E402


def _png(w=64, h=32, color=(10, 200, 30)) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (w, h), color).save(buf, format="PNG")
    return buf.getvalue()


def test_decode_gives_rgb_uint8_hwc():
    rgb = img.decode(_png(64, 32))
    assert rgb.shape == (32, 64, 3) and rgb.dtype == np.uint8
    assert tuple(rgb[0, 0]) == (10, 200, 30)


def test_encode_jpeg_round_trips_through_decode():
    rgb = img.decode(_png(40, 20, (255, 0, 0)))
    data = img.encode_jpeg(rgb, quality=90)
    assert data[:2] == b"\xff\xd8"
    back = img.decode(data)
    assert back.shape == (20, 40, 3) and back[10, 20, 0] > 200


def test_resize_long_edge_never_upscales():
    rgb = img.decode(_png(800, 500))
    small = img.resize_long_edge(rgb, 320)
    assert small.shape == (200, 320, 3)
    assert img.resize_long_edge(small, 800).shape == (200, 320, 3)


def test_crop_uses_normalised_box_and_clamps():
    rgb = np.zeros((100, 200, 3), dtype=np.uint8)
    rgb[:, 100:, :] = 255
    right = img.crop(rgb, (0.5, 0.0, 1.0, 1.0))
    assert right.shape == (100, 100, 3) and right.min() == 255
    assert img.crop(rgb, (-1.0, -1.0, 2.0, 2.0)).shape == (100, 200, 3)


def test_grey_small_is_float_and_fixed_size():
    g = img.grey_small(img.decode(_png(640, 360)))
    assert g.shape == (90, 160) and g.dtype == np.float32
    assert 0.0 <= g.min() and g.max() <= 255.0


@pytest.mark.parametrize("name,box", [
    ("left", (0.0, 0.0, 0.5, 1.0)), ("right", (0.5, 0.0, 1.0, 1.0)),
    ("centre", (0.25, 0.25, 0.75, 0.75)), ("center", (0.25, 0.25, 0.75, 0.75)),
    ("top", (0.0, 0.0, 1.0, 0.5)), ("bottom", (0.0, 0.5, 1.0, 1.0)),
    ("0.1,0.2,0.3,0.4", (0.1, 0.2, 0.3, 0.4)),
])
def test_region_box(name, box):
    assert img.region_box(name) == pytest.approx(box)


@pytest.mark.parametrize("bad", ["nowhere", "0.5,0.5,0.2,0.9", "1,2", "a,b,c,d"])
def test_region_box_rejects(bad):
    with pytest.raises(ValueError):
        img.region_box(bad)


def test_data_url_prefix():
    assert img.data_url(b"\xff\xd8abc").startswith("data:image/jpeg;base64,")
```

- [ ] **Step 3: Run the tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_perception_image.py -q`
Expected: FAIL, `ModuleNotFoundError: No module named 'richard.perception'`.

- [ ] **Step 4: Implement**

Create `src/richard/perception/__init__.py`:

```python
"""Ambient perception (spec 3b): frames in, events out, snapshots on request.

Sensor (motion, person, face) → gate (debounce, transitions, cooldowns, quiet
hours) → brain (context items, control-loop events, the camera tool). Pillow and
onnxruntime are imported lazily inside this package only: the lean core stays
numpy-only and `richard serve` starts without them unless the plugin is enabled.
"""
```

Create `src/richard/perception/image.py`:

```python
"""Pixel helpers. The only module that touches Pillow; everything else sees numpy."""
from __future__ import annotations

import base64
import io

import numpy as np

REGIONS = {
    "left": (0.0, 0.0, 0.5, 1.0),
    "right": (0.5, 0.0, 1.0, 1.0),
    "centre": (0.25, 0.25, 0.75, 0.75),
    "center": (0.25, 0.25, 0.75, 0.75),
    "top": (0.0, 0.0, 1.0, 0.5),
    "bottom": (0.0, 0.5, 1.0, 1.0),
}


def decode(data: bytes) -> np.ndarray:
    """JPEG/PNG/WebP bytes → HxWx3 RGB uint8."""
    from PIL import Image

    with Image.open(io.BytesIO(data)) as im:
        return np.asarray(im.convert("RGB"), dtype=np.uint8).copy()


def encode_jpeg(rgb: np.ndarray, quality: int = 85) -> bytes:
    from PIL import Image

    buf = io.BytesIO()
    Image.fromarray(np.ascontiguousarray(rgb, dtype=np.uint8), "RGB").save(buf, format="JPEG", quality=quality)
    return buf.getvalue()


def resize_long_edge(rgb: np.ndarray, max_edge: int) -> np.ndarray:
    """Downscale so the long edge is `max_edge`; never upscale."""
    from PIL import Image

    h, w = rgb.shape[:2]
    scale = min(1.0, max_edge / max(h, w))
    if scale >= 1.0:
        return rgb
    size = (max(1, round(w * scale)), max(1, round(h * scale)))
    im = Image.fromarray(np.ascontiguousarray(rgb, dtype=np.uint8), "RGB").resize(size, Image.BILINEAR)
    return np.asarray(im, dtype=np.uint8).copy()


def crop(rgb: np.ndarray, box: tuple[float, float, float, float]) -> np.ndarray:
    """Crop a normalised (x0, y0, x1, y1) box, clamped to the frame."""
    h, w = rgb.shape[:2]
    x0 = min(max(int(box[0] * w), 0), w - 1)
    y0 = min(max(int(box[1] * h), 0), h - 1)
    x1 = min(max(int(round(box[2] * w)), x0 + 1), w)
    y1 = min(max(int(round(box[3] * h)), y0 + 1), h)
    return rgb[y0:y1, x0:x1].copy()


def grey_small(rgb: np.ndarray, width: int = 160, height: int = 90) -> np.ndarray:
    """A small greyscale float32 copy for motion work."""
    from PIL import Image

    im = Image.fromarray(np.ascontiguousarray(rgb, dtype=np.uint8), "RGB").convert("L").resize((width, height), Image.BILINEAR)
    return np.asarray(im, dtype=np.float32)


def region_box(region: str) -> tuple[float, float, float, float]:
    """A named region or 'x0,y0,x1,y1' in normalised coordinates. Raises ValueError."""
    key = (region or "").strip().lower()
    if key in REGIONS:
        return REGIONS[key]
    parts = key.split(",")
    if len(parts) != 4:
        raise ValueError("region must be left|right|centre|top|bottom or x0,y0,x1,y1")
    try:
        x0, y0, x1, y1 = (float(p) for p in parts)
    except ValueError as exc:
        raise ValueError("region coordinates must be numbers in 0..1") from exc
    if not (0.0 <= x0 < x1 <= 1.0 and 0.0 <= y0 < y1 <= 1.0):
        raise ValueError("region coordinates must satisfy 0 <= x0 < x1 <= 1 and 0 <= y0 < y1 <= 1")
    return (x0, y0, x1, y1)


def data_url(jpeg: bytes) -> str:
    return "data:image/jpeg;base64," + base64.b64encode(jpeg).decode()
```

- [ ] **Step 5: Run the tests to verify they pass, then commit**

Run: `.venv/bin/python -m pytest tests/test_perception_image.py -q`
Expected: all pass.

```bash
git add pyproject.toml src/richard/perception/__init__.py src/richard/perception/image.py tests/test_perception_image.py
git commit -m "perception: image helpers behind a new perception extra (Pillow, onnxruntime)

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 2: Frames: sources and the hub

**Files:**
- Create: `src/richard/perception/frames.py`
- Test: `tests/test_perception_frames.py`

**Interfaces:**
- Produces: `Frame(ts: float, rgb: np.ndarray, source_id: str)`; `FrameSource` protocol with `source_id: str`, `latest() -> Frame | None`, `native_jpeg() -> bytes | None`; `PushFrameSource(source_id, *, clock=time.monotonic)` with `push_jpeg(data: bytes) -> Frame` (decodes via `image.decode`), `latest()`, `native_jpeg()`; `DirectoryFrameSource(source_id, directory, *, fps=2.0, loop=False, clock=...)` replaying `*.jpg|*.png` in sorted order, `advance()` to step manually; `FrameHub(stale_s=5.0, clock=...)` with `add(source)`, `remove(source_id)`, `sources() -> list[str]`, `latest(source_id) -> Frame | None`, `native_jpeg(source_id)`, `is_stale(source_id) -> bool`.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_perception_frames.py`:

```python
import io

import numpy as np
import pytest

pytest.importorskip("PIL")
from PIL import Image  # noqa: E402

from richard.perception.frames import DirectoryFrameSource, Frame, FrameHub, PushFrameSource  # noqa: E402


def _jpeg(color=(0, 0, 255), w=32, h=16) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (w, h), color).save(buf, format="JPEG")
    return buf.getvalue()


class Clock:
    def __init__(self, t=100.0):
        self.t = t

    def __call__(self):
        return self.t


def test_push_source_decodes_and_keeps_the_native_jpeg():
    clock = Clock()
    src = PushFrameSource("browser", clock=clock)
    assert src.latest() is None and src.native_jpeg() is None
    data = _jpeg()
    frame = src.push_jpeg(data)
    assert isinstance(frame, Frame) and frame.source_id == "browser" and frame.ts == 100.0
    assert frame.rgb.shape == (16, 32, 3)
    assert src.native_jpeg() == data
    assert src.latest() is frame


def test_push_source_rejects_undecodable_bytes():
    src = PushFrameSource("browser")
    with pytest.raises(ValueError):
        src.push_jpeg(b"not an image")


def test_directory_source_replays_in_sorted_order(tmp_path):
    for i, color in enumerate([(255, 0, 0), (0, 255, 0)]):
        Image.new("RGB", (8, 8), color).save(tmp_path / f"{i:03d}.png")
    src = DirectoryFrameSource("replay", tmp_path)
    assert src.latest() is None
    f1 = src.advance()
    assert tuple(f1.rgb[0, 0]) == (255, 0, 0)
    f2 = src.advance()
    assert tuple(f2.rgb[0, 0]) == (0, 255, 0) and src.latest() is f2
    assert src.advance() is None  # end, no loop
    assert src.latest() is f2


def test_hub_tracks_sources_and_staleness():
    clock = Clock()
    hub = FrameHub(stale_s=5.0, clock=clock)
    src = PushFrameSource("browser", clock=clock)
    hub.add(src)
    assert hub.sources() == ["browser"]
    assert hub.latest("browser") is None and hub.is_stale("browser") is True
    src.push_jpeg(_jpeg())
    assert hub.latest("browser").ts == 100.0 and hub.is_stale("browser") is False
    clock.t = 106.0
    assert hub.is_stale("browser") is True
    assert hub.native_jpeg("browser") is not None
    hub.remove("browser")
    assert hub.sources() == [] and hub.latest("browser") is None
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_perception_frames.py -q`
Expected: FAIL, `ModuleNotFoundError`.

- [ ] **Step 3: Implement**

Create `src/richard/perception/frames.py`:

```python
"""Frame sources and the hub that holds the latest frame of each.

A source only has to answer "what is the latest frame" and "give me your native
JPEG". The browser pushes; the robot (spec four's body plugin) pulls from the SDK;
a directory replays files for tests. Nothing here runs a thread: the pipeline
(pipeline.py) polls the hub at the stream rate.
"""
from __future__ import annotations

import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

import numpy as np

from richard.perception import image


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
```

- [ ] **Step 4: Run the tests to verify they pass, then commit**

Run: `.venv/bin/python -m pytest tests/test_perception_frames.py -q`

```bash
git add src/richard/perception/frames.py tests/test_perception_frames.py
git commit -m "perception: frame sources (push, directory replay) and the frame hub

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 3: Motion and stillness (numpy only)

**Files:**
- Create: `src/richard/perception/motion.py`
- Test: `tests/test_perception_motion.py`

**Interfaces:**
- Produces: `MotionDetector(*, threshold=0.06, scene_change_ratio=0.5, scene_change_hold_s=3.0, background_alpha=0.02)` with `feed(grey: np.ndarray, ts: float) -> MotionReading`; `MotionReading(activity: float, scene_changed: bool)`; `StillnessTracker(*, still_after_s=600.0)` with `feed(activity: float, threshold: float, ts: float) -> float | None` (returns minutes still when a `stillness` event is due, once per still period) and `.still_since -> float | None`.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_perception_motion.py`:

```python
import numpy as np

from richard.perception.motion import MotionDetector, MotionReading, StillnessTracker


def _grey(value=50.0):
    return np.full((90, 160), value, dtype=np.float32)


def test_first_frame_has_no_activity():
    det = MotionDetector()
    reading = det.feed(_grey(), ts=0.0)
    assert reading == MotionReading(activity=0.0, scene_changed=False)


def test_local_change_yields_proportional_activity():
    det = MotionDetector()
    det.feed(_grey(), ts=0.0)
    moved = _grey()
    moved[:, :16] = 200.0  # 10 % of the pixels change a lot
    reading = det.feed(moved, ts=0.5)
    assert 0.08 < reading.activity < 0.12
    assert reading.scene_changed is False


def test_scene_change_needs_a_large_persistent_difference_from_the_background():
    det = MotionDetector(scene_change_hold_s=3.0)
    for i in range(10):
        det.feed(_grey(50.0), ts=i * 0.5)  # background settles at 50
    bright = _grey(200.0)  # the lights went on
    assert det.feed(bright, ts=5.0).scene_changed is False   # not yet persistent
    assert det.feed(bright, ts=7.0).scene_changed is False
    assert det.feed(bright, ts=8.1).scene_changed is True    # held for > 3 s
    assert det.feed(bright, ts=8.6).scene_changed is False   # reported once


def test_stillness_tracker_fires_once_per_still_period():
    tracker = StillnessTracker(still_after_s=60.0)
    assert tracker.feed(0.0, threshold=0.06, ts=0.0) is None
    assert tracker.still_since == 0.0
    assert tracker.feed(0.0, threshold=0.06, ts=30.0) is None
    assert tracker.feed(0.0, threshold=0.06, ts=61.0) == 1.0  # ~1 minute still
    assert tracker.feed(0.0, threshold=0.06, ts=120.0) is None  # already reported
    assert tracker.feed(0.5, threshold=0.06, ts=121.0) is None  # movement resets
    assert tracker.still_since is None
    assert tracker.feed(0.0, threshold=0.06, ts=122.0) is None
    assert tracker.still_since == 122.0
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_perception_motion.py -q`
Expected: FAIL, `ModuleNotFoundError`.

- [ ] **Step 3: Implement**

Create `src/richard/perception/motion.py`:

```python
"""Cheapest stage first: frame differencing on a 160x90 grey image.

activity = fraction of pixels whose grey level moved by more than 25 since the
previous frame. A scene change is a large difference from a slowly adapting
background that persists (lights on, a moved camera), reported once per episode.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np

PIXEL_DELTA = 25.0


@dataclass(frozen=True)
class MotionReading:
    activity: float
    scene_changed: bool


class MotionDetector:
    def __init__(self, *, threshold: float = 0.06, scene_change_ratio: float = 0.5,
                 scene_change_hold_s: float = 3.0, background_alpha: float = 0.02) -> None:
        self.threshold = threshold
        self._scene_ratio = scene_change_ratio
        self._hold_s = scene_change_hold_s
        self._alpha = background_alpha
        self._previous: np.ndarray | None = None
        self._background: np.ndarray | None = None
        self._changed_since: float | None = None
        self._reported = False

    def feed(self, grey: np.ndarray, ts: float) -> MotionReading:
        grey = grey.astype(np.float32, copy=False)
        if self._previous is None:
            self._previous = grey
            self._background = grey.copy()
            return MotionReading(activity=0.0, scene_changed=False)
        activity = float(np.mean(np.abs(grey - self._previous) > PIXEL_DELTA))
        self._previous = grey
        away = float(np.mean(np.abs(grey - self._background) > PIXEL_DELTA))
        scene_changed = False
        if away >= self._scene_ratio:
            if self._changed_since is None:
                self._changed_since = ts
            elif not self._reported and ts - self._changed_since > self._hold_s:
                scene_changed = True
                self._reported = True
        else:
            self._changed_since = None
            self._reported = False
        # The background follows the scene slowly, so a new arrangement becomes normal.
        self._background = (1.0 - self._alpha) * self._background + self._alpha * grey
        return MotionReading(activity=activity, scene_changed=scene_changed)


class StillnessTracker:
    """Minutes without movement; reports once when the still period passes the bar."""

    def __init__(self, *, still_after_s: float = 600.0) -> None:
        self._after = still_after_s
        self.still_since: float | None = None
        self._reported = False

    def feed(self, activity: float, threshold: float, ts: float) -> float | None:
        if activity > threshold:
            self.still_since = None
            self._reported = False
            return None
        if self.still_since is None:
            self.still_since = ts
            return None
        if not self._reported and ts - self.still_since >= self._after:
            self._reported = True
            return round((ts - self.still_since) / 60.0, 1)
        return None
```

- [ ] **Step 4: Run the tests to verify they pass, then commit**

Run: `.venv/bin/python -m pytest tests/test_perception_motion.py -q`

```bash
git add src/richard/perception/motion.py tests/test_perception_motion.py
git commit -m "perception: motion detector and stillness tracker (numpy only)

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 4: Model registry, download, onnxruntime sessions

**Files:**
- Create: `src/richard/perception/models.py`
- Test: `tests/test_perception_models.py`

**Interfaces:**
- Produces: `ModelSpec(name, file, url, min_bytes, licence)`; `MODELS: dict[str, ModelSpec]` with keys `ssd_mobilenet_v1`, `ultraface_rfb_320`, `arcface_r100_int8`; `ensure_model(name, *, models_dir=None, client=None, write=print) -> Path`; `session_options(threads: int = 4)`; `make_session(path, *, device="cpu", threads=4)` (onnxruntime `InferenceSession`, CPU provider, or CUDA first when `device == "cuda"`).

- [ ] **Step 1: Write the failing tests**

Create `tests/test_perception_models.py`:

```python
import pytest

from richard.perception.models import MODELS, ensure_model, session_options


class _Resp:
    def __init__(self, content, status=200):
        self.content = content
        self.status = status

    def raise_for_status(self):
        if self.status != 200:
            raise RuntimeError(f"http {self.status}")


class _Client:
    def __init__(self, content=b"x" * 2_000_000, status=200):
        self.calls = []
        self._content = content
        self._status = status

    def get(self, url):
        self.calls.append(url)
        return _Resp(self._content, self._status)


def test_registry_lists_the_three_models_with_permissive_licences():
    assert set(MODELS) == {"ssd_mobilenet_v1", "ultraface_rfb_320", "arcface_r100_int8"}
    assert MODELS["ssd_mobilenet_v1"].file == "ssd_mobilenet_v1_12.onnx"
    assert MODELS["ultraface_rfb_320"].file == "version-RFB-320.onnx"
    assert MODELS["arcface_r100_int8"].file == "arcfaceresnet100-11-int8.onnx"
    assert all(spec.licence in ("MIT", "Apache-2.0") for spec in MODELS.values())


def test_ensure_model_downloads_once_and_reuses(tmp_path):
    client = _Client()
    path = ensure_model("ultraface_rfb_320", models_dir=tmp_path, client=client, write=lambda s: None)
    assert path == tmp_path / "version-RFB-320.onnx" and path.stat().st_size == 2_000_000
    ensure_model("ultraface_rfb_320", models_dir=tmp_path, client=client, write=lambda s: None)
    assert client.calls == [MODELS["ultraface_rfb_320"].url]


def test_ensure_model_rejects_a_truncated_download(tmp_path):
    client = _Client(content=b"tiny")
    with pytest.raises(RuntimeError, match="smaller than expected"):
        ensure_model("ultraface_rfb_320", models_dir=tmp_path, client=client, write=lambda s: None)
    assert not (tmp_path / "version-RFB-320.onnx").exists()


def test_ensure_model_unknown_name():
    with pytest.raises(KeyError):
        ensure_model("nope", models_dir="/nonexistent")


def test_session_options_fix_the_thread_count():
    pytest.importorskip("onnxruntime")
    opts = session_options(threads=3)
    assert opts.intra_op_num_threads == 3 and opts.inter_op_num_threads == 1
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_perception_models.py -q`
Expected: FAIL, `ModuleNotFoundError`.

- [ ] **Step 3: Implement**

Create `src/richard/perception/models.py`:

```python
"""The ONNX models perception runs, fetched on first use like Silero VAD.

Chosen 2026-09-09 for permissive licences and simple post-processing:
SSD-MobileNetV1 (ONNX model zoo, MIT) has NMS inside the graph; UltraFace RFB-320
(ONNX model zoo, MIT) is 1.3 MB; ArcFace ResNet100 int8 (ONNX model zoo, Apache-2.0)
gives 512-d embeddings. Thread counts are fixed explicitly: onnxruntime's default
affinity pinning fails noisily inside the LXC (pthread_setaffinity_np errors).
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from richard.realtime.vad import default_models_dir

_ZOO = "https://github.com/onnx/models/raw/main/validated/vision"


@dataclass(frozen=True)
class ModelSpec:
    name: str
    file: str
    url: str
    min_bytes: int
    licence: str


MODELS: dict[str, ModelSpec] = {
    "ssd_mobilenet_v1": ModelSpec(
        "ssd_mobilenet_v1", "ssd_mobilenet_v1_12.onnx",
        f"{_ZOO}/object_detection_segmentation/ssd-mobilenetv1/model/ssd_mobilenet_v1_12.onnx",
        25_000_000, "MIT"),
    "ultraface_rfb_320": ModelSpec(
        "ultraface_rfb_320", "version-RFB-320.onnx",
        f"{_ZOO}/body_analysis/ultraface/models/version-RFB-320.onnx",
        1_000_000, "MIT"),
    "arcface_r100_int8": ModelSpec(
        "arcface_r100_int8", "arcfaceresnet100-11-int8.onnx",
        f"{_ZOO}/body_analysis/arcface/model/arcfaceresnet100-11-int8.onnx",
        60_000_000, "Apache-2.0"),
}


def ensure_model(name: str, *, models_dir: Path | str | None = None, client=None, write=print) -> Path:
    spec = MODELS[name]
    models_dir = Path(models_dir) if models_dir is not None else default_models_dir()
    models_dir.mkdir(parents=True, exist_ok=True)
    target = models_dir / spec.file
    if target.exists() and target.stat().st_size >= spec.min_bytes:
        return target
    if client is None:
        import httpx

        client = httpx.Client(timeout=300.0, follow_redirects=True)
    write(f"Downloading {spec.file} ({spec.licence}) for perception...")
    try:
        resp = client.get(spec.url)
        resp.raise_for_status()
    except Exception as exc:
        raise RuntimeError(f"Failed to download {spec.file} from {spec.url}: {exc}") from exc
    if len(resp.content) < spec.min_bytes:
        raise RuntimeError(f"{spec.file}: download smaller than expected ({len(resp.content)} bytes)")
    tmp = target.with_suffix(".part")
    tmp.write_bytes(resp.content)
    tmp.replace(target)
    return target


def session_options(threads: int = 4):
    import onnxruntime

    opts = onnxruntime.SessionOptions()
    opts.intra_op_num_threads = max(1, int(threads))
    opts.inter_op_num_threads = 1
    opts.log_severity_level = 3  # errors only; the affinity warnings are noise
    return opts


def make_session(path, *, device: str = "cpu", threads: int = 4):
    import onnxruntime

    providers = ["CPUExecutionProvider"]
    if device == "cuda" and "CUDAExecutionProvider" in onnxruntime.get_available_providers():
        providers = ["CUDAExecutionProvider", "CPUExecutionProvider"]
    return onnxruntime.InferenceSession(str(path), sess_options=session_options(threads), providers=providers)
```

- [ ] **Step 4: Run the tests to verify they pass, then commit**

Run: `.venv/bin/python -m pytest tests/test_perception_models.py -q`

```bash
git add src/richard/perception/models.py tests/test_perception_models.py
git commit -m "perception: model registry, first-use download, onnxruntime sessions with fixed threads

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 5: Person detector (SSD-MobileNetV1)

**Files:**
- Create: `src/richard/perception/detect.py`
- Test: `tests/test_perception_detect.py`

**Interfaces:**
- Produces: `Detection(box: tuple[float, float, float, float], score: float)` (normalised `x0, y0, x1, y1`); `PersonDetector(session, *, threshold=0.55, work_width=320)` with `detect(rgb: np.ndarray) -> list[Detection]`; `PersonDetector.load(*, device="cpu", threads=4, models_dir=None, write=print)` classmethod that calls `ensure_model("ssd_mobilenet_v1")` and `make_session`.
- Model I/O (from the model zoo README): input `image_tensor:0` uint8 `[1, H, W, 3]`; outputs `detection_boxes:0` `[1, N, 4]` as `(top, left, bottom, right)` normalised, `detection_classes:0` `[1, N]` (COCO, person = 1), `detection_scores:0` `[1, N]`, `num_detections:0` `[1]`.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_perception_detect.py`:

```python
import numpy as np

from richard.perception.detect import Detection, PersonDetector


class FakeSession:
    """Mimics ssd_mobilenet_v1_12.onnx: named outputs, boxes as (top, left, bottom, right)."""

    def __init__(self, boxes, classes, scores):
        self.boxes, self.classes, self.scores = boxes, classes, scores
        self.inputs = []

    def run(self, output_names, feeds):
        self.inputs.append(feeds)
        n = len(self.boxes)
        out = {
            "detection_boxes:0": np.array([self.boxes], dtype=np.float32),
            "detection_classes:0": np.array([self.classes], dtype=np.float32),
            "detection_scores:0": np.array([self.scores], dtype=np.float32),
            "num_detections:0": np.array([n], dtype=np.float32),
        }
        return [out[name] for name in output_names]


def test_detect_keeps_persons_above_threshold_in_xyxy_order():
    session = FakeSession(
        boxes=[[0.1, 0.2, 0.9, 0.6], [0.0, 0.0, 0.5, 0.5], [0.2, 0.2, 0.3, 0.3]],
        classes=[1, 3, 1], scores=[0.9, 0.95, 0.3],
    )
    det = PersonDetector(session, threshold=0.55)
    rgb = np.zeros((480, 640, 3), dtype=np.uint8)
    out = det.detect(rgb)
    assert out == [Detection(box=(0.2, 0.1, 0.6, 0.9), score=0.9)]


def test_detect_feeds_uint8_nhwc_at_work_width():
    session = FakeSession(boxes=[], classes=[], scores=[])
    det = PersonDetector(session, work_width=320)
    det.detect(np.zeros((900, 1600, 3), dtype=np.uint8))
    tensor = session.inputs[0]["image_tensor:0"]
    assert tensor.dtype == np.uint8 and tensor.shape == (1, 180, 320, 3)
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_perception_detect.py -q`
Expected: FAIL, `ModuleNotFoundError`.

- [ ] **Step 3: Implement**

Create `src/richard/perception/detect.py`:

```python
"""People in the frame: SSD-MobileNetV1 from the ONNX model zoo (MIT).

The graph does its own NMS and takes any image size (it resizes to 300x300
inside), so the work here is a resize to 320 px wide, one run, and a filter on
class 1 (COCO person) above the threshold. ~40 ms on the CT's CPU cores.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from richard.perception import image
from richard.perception.models import ensure_model, make_session

PERSON_CLASS = 1
OUTPUTS = ["detection_boxes:0", "detection_classes:0", "detection_scores:0", "num_detections:0"]


@dataclass(frozen=True)
class Detection:
    box: tuple[float, float, float, float]  # normalised x0, y0, x1, y1
    score: float


class PersonDetector:
    def __init__(self, session, *, threshold: float = 0.55, work_width: int = 320) -> None:
        self._session = session
        self.threshold = threshold
        self._work_width = work_width

    @classmethod
    def load(cls, *, device: str = "cpu", threads: int = 4, models_dir=None, write=print, **kwargs) -> "PersonDetector":
        path = ensure_model("ssd_mobilenet_v1", models_dir=models_dir, write=write)
        return cls(make_session(path, device=device, threads=threads), **kwargs)

    def detect(self, rgb: np.ndarray) -> list[Detection]:
        small = image.resize_long_edge(rgb, self._work_width) if rgb.shape[1] > self._work_width else rgb
        tensor = np.ascontiguousarray(small, dtype=np.uint8)[np.newaxis, ...]
        boxes, classes, scores, count = self._session.run(OUTPUTS, {"image_tensor:0": tensor})
        n = int(count[0])
        found = []
        for i in range(n):
            if int(round(float(classes[0][i]))) != PERSON_CLASS or float(scores[0][i]) < self.threshold:
                continue
            top, left, bottom, right = (round(float(v), 4) for v in boxes[0][i])
            found.append(Detection(box=(left, top, right, bottom), score=round(float(scores[0][i]), 4)))
        return found
```

- [ ] **Step 4: Run the tests to verify they pass, then commit**

Run: `.venv/bin/python -m pytest tests/test_perception_detect.py -q`

```bash
git add src/richard/perception/detect.py tests/test_perception_detect.py
git commit -m "perception: person detector on SSD-MobileNetV1

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 6: Faces: detector, embedder, gallery, identifier

**Files:**
- Modify: `src/richard/perception/image.py` (add `resize_exact`)
- Create: `src/richard/perception/faces.py`
- Test: `tests/test_perception_faces.py`

**Interfaces:**
- Produces: `image.resize_exact(rgb, width, height) -> np.ndarray`; `nms(boxes: np.ndarray, scores: np.ndarray, iou: float) -> list[int]`; `FaceDetector(session, *, threshold=0.7, iou=0.4)` with `detect(rgb) -> list[Detection]` and `load(...)`; `FaceEmbedder(session)` with `embed(rgb, box) -> np.ndarray` (unit-length float32 512) and `load(...)`; `Gallery(path)` with `enrol(name, vectors: list[np.ndarray]) -> int`, `match(vector, threshold) -> tuple[str, float] | None`, `delete(name) -> bool`, `list() -> list[dict]`, `close()`; `FaceMatch(box, name: str | None, score: float)`; `FaceIdentifier(detector, embedder, gallery, *, threshold=0.45)` with `identify(rgb) -> list[FaceMatch]`.
- Model I/O: UltraFace input `input` float32 `[1, 3, 240, 320]` RGB, `(x - 127) / 128`; outputs `scores` `[1, 4420, 2]` (column 1 = face) and `boxes` `[1, 4420, 4]` normalised `x0, y0, x1, y1`. ArcFace input `data` float32 `[1, 3, 112, 112]` RGB 0-255; output `[1, 512]`.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_perception_faces.py`:

```python
import numpy as np
import pytest

pytest.importorskip("PIL")

from richard.perception import image  # noqa: E402
from richard.perception.detect import Detection  # noqa: E402
from richard.perception.faces import FaceDetector, FaceEmbedder, FaceIdentifier, FaceMatch, Gallery, nms  # noqa: E402


class FakeFaceSession:
    def __init__(self, faces):  # faces: list of (x0, y0, x1, y1, prob)
        self.faces = faces
        self.feeds = []

    def run(self, output_names, feeds):
        self.feeds.append(feeds)
        n = 4420
        scores = np.zeros((1, n, 2), dtype=np.float32)
        boxes = np.zeros((1, n, 4), dtype=np.float32)
        scores[0, :, 0] = 1.0
        for i, (x0, y0, x1, y1, p) in enumerate(self.faces):
            scores[0, i] = (1 - p, p)
            boxes[0, i] = (x0, y0, x1, y1)
        return [scores, boxes]


class FakeEmbedSession:
    def __init__(self, vec):
        self.vec = np.asarray(vec, dtype=np.float32)
        self.feeds = []

    def run(self, output_names, feeds):
        self.feeds.append(feeds)
        return [self.vec[np.newaxis, :]]


def test_nms_suppresses_overlapping_boxes():
    boxes = np.array([[0, 0, 10, 10], [1, 1, 11, 11], [50, 50, 60, 60]], dtype=np.float32)
    scores = np.array([0.9, 0.8, 0.7], dtype=np.float32)
    assert nms(boxes, scores, iou=0.4) == [0, 2]


def test_face_detector_preprocesses_and_filters():
    session = FakeFaceSession([(0.1, 0.1, 0.3, 0.4, 0.95), (0.11, 0.1, 0.31, 0.4, 0.9), (0.6, 0.6, 0.7, 0.8, 0.2)])
    det = FaceDetector(session, threshold=0.7, iou=0.4)
    out = det.detect(np.full((480, 640, 3), 127, dtype=np.uint8))
    assert out == [Detection(box=(0.1, 0.1, 0.3, 0.4), score=0.95)]
    tensor = session.feeds[0]["input"]
    assert tensor.shape == (1, 3, 240, 320) and tensor.dtype == np.float32
    assert abs(float(tensor.mean())) < 0.01  # (127 - 127) / 128


def test_face_embedder_crops_with_margin_and_normalises():
    session = FakeEmbedSession([3.0, 4.0] + [0.0] * 510)
    emb = FaceEmbedder(session)
    vec = emb.embed(np.zeros((200, 200, 3), dtype=np.uint8), (0.4, 0.4, 0.6, 0.6))
    assert vec.shape == (512,) and abs(float(np.linalg.norm(vec)) - 1.0) < 1e-5
    tensor = session.feeds[0]["data"]
    assert tensor.shape == (1, 3, 112, 112) and tensor.dtype == np.float32


def test_gallery_enrol_match_delete(tmp_path):
    g = Gallery(tmp_path / "faces.db")
    a = np.zeros(512, dtype=np.float32); a[0] = 1.0
    b = np.zeros(512, dtype=np.float32); b[1] = 1.0
    assert g.enrol("matteo", [a]) == 1
    assert g.enrol("guest", [b, b]) == 2
    assert [(p["name"], p["samples"]) for p in g.list()] == [("guest", 2), ("matteo", 1)]
    probe = np.zeros(512, dtype=np.float32); probe[0] = 0.9; probe[1] = 0.1
    name, score = g.match(probe, threshold=0.45)
    assert name == "matteo" and score > 0.9
    assert g.match(np.zeros(512, dtype=np.float32), threshold=0.45) is None
    assert g.delete("matteo") is True and g.delete("matteo") is False
    assert g.match(probe, threshold=0.45) is None
    g.close()


def test_identifier_names_known_faces_and_flags_unknown(tmp_path):
    g = Gallery(tmp_path / "faces.db")
    known = np.zeros(512, dtype=np.float32); known[2] = 1.0
    g.enrol("matteo", [known])
    detector = FaceDetector(FakeFaceSession([(0.1, 0.1, 0.3, 0.4, 0.95)]))
    ident = FaceIdentifier(detector, FaceEmbedder(FakeEmbedSession(known)), g, threshold=0.45)
    assert ident.identify(np.zeros((100, 100, 3), dtype=np.uint8)) == [FaceMatch(box=(0.1, 0.1, 0.3, 0.4), name="matteo", score=pytest.approx(1.0))]
    other = np.zeros(512, dtype=np.float32); other[5] = 1.0
    ident2 = FaceIdentifier(detector, FaceEmbedder(FakeEmbedSession(other)), g, threshold=0.45)
    assert ident2.identify(np.zeros((100, 100, 3), dtype=np.uint8))[0].name is None
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_perception_faces.py -q`
Expected: FAIL, `ImportError`.

- [ ] **Step 3: Implement**

Add to `src/richard/perception/image.py`:

```python
def resize_exact(rgb: np.ndarray, width: int, height: int) -> np.ndarray:
    from PIL import Image

    im = Image.fromarray(np.ascontiguousarray(rgb, dtype=np.uint8), "RGB").resize((width, height), Image.BILINEAR)
    return np.asarray(im, dtype=np.uint8).copy()
```

Create `src/richard/perception/faces.py`:

```python
"""Who is in the frame: UltraFace (MIT) finds faces, ArcFace int8 (Apache-2.0) embeds
them, a local sqlite gallery names them. Opt-in, local only, deletable.

No landmark alignment: the crop is the detector's box widened by 20 %. That costs
some embedding stability; the gate's "two agreeing matches" rule absorbs it. If
identification proves unreliable in the house, a 5-point landmark model is the
next step, not a bigger embedder.
"""
from __future__ import annotations

import sqlite3
import time
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from richard.perception import image
from richard.perception.detect import Detection
from richard.perception.models import ensure_model, make_session

FACE_INPUT = (320, 240)
EMBED_SIZE = 112


def nms(boxes: np.ndarray, scores: np.ndarray, iou: float) -> list[int]:
    """Greedy non-maximum suppression; boxes are x0, y0, x1, y1. Returns kept indices."""
    order = np.argsort(-scores)
    keep: list[int] = []
    while order.size:
        i = int(order[0])
        keep.append(i)
        if order.size == 1:
            break
        rest = order[1:]
        xx0 = np.maximum(boxes[i, 0], boxes[rest, 0])
        yy0 = np.maximum(boxes[i, 1], boxes[rest, 1])
        xx1 = np.minimum(boxes[i, 2], boxes[rest, 2])
        yy1 = np.minimum(boxes[i, 3], boxes[rest, 3])
        inter = np.clip(xx1 - xx0, 0, None) * np.clip(yy1 - yy0, 0, None)
        area_i = (boxes[i, 2] - boxes[i, 0]) * (boxes[i, 3] - boxes[i, 1])
        area_r = (boxes[rest, 2] - boxes[rest, 0]) * (boxes[rest, 3] - boxes[rest, 1])
        overlap = inter / np.maximum(area_i + area_r - inter, 1e-9)
        order = rest[overlap <= iou]
    return keep


class FaceDetector:
    def __init__(self, session, *, threshold: float = 0.7, iou: float = 0.4) -> None:
        self._session = session
        self.threshold = threshold
        self._iou = iou

    @classmethod
    def load(cls, *, device: str = "cpu", threads: int = 2, models_dir=None, write=print, **kwargs) -> "FaceDetector":
        path = ensure_model("ultraface_rfb_320", models_dir=models_dir, write=write)
        return cls(make_session(path, device=device, threads=threads), **kwargs)

    def detect(self, rgb: np.ndarray) -> list[Detection]:
        small = image.resize_exact(rgb, *FACE_INPUT).astype(np.float32)
        tensor = ((small - 127.0) / 128.0).transpose(2, 0, 1)[np.newaxis, ...].astype(np.float32)
        scores, boxes = self._session.run(["scores", "boxes"], {"input": np.ascontiguousarray(tensor)})
        probs = scores[0, :, 1]
        mask = probs >= self.threshold
        if not mask.any():
            return []
        cand_boxes = boxes[0][mask].astype(np.float32)
        cand_scores = probs[mask].astype(np.float32)
        keep = nms(cand_boxes, cand_scores, self._iou)
        return [Detection(box=tuple(round(float(v), 4) for v in cand_boxes[k]), score=round(float(cand_scores[k]), 4))
                for k in keep]


class FaceEmbedder:
    def __init__(self, session) -> None:
        self._session = session

    @classmethod
    def load(cls, *, device: str = "cpu", threads: int = 2, models_dir=None, write=print) -> "FaceEmbedder":
        path = ensure_model("arcface_r100_int8", models_dir=models_dir, write=write)
        return cls(make_session(path, device=device, threads=threads))

    def embed(self, rgb: np.ndarray, box: tuple[float, float, float, float]) -> np.ndarray:
        x0, y0, x1, y1 = box
        mx, my = (x1 - x0) * 0.2, (y1 - y0) * 0.2
        face = image.crop(rgb, (x0 - mx, y0 - my, x1 + mx, y1 + my))
        square = image.resize_exact(face, EMBED_SIZE, EMBED_SIZE).astype(np.float32)
        tensor = np.ascontiguousarray(square.transpose(2, 0, 1)[np.newaxis, ...], dtype=np.float32)
        (out,) = self._session.run(None, {"data": tensor})
        vec = np.asarray(out, dtype=np.float32).reshape(-1)
        return vec / max(float(np.linalg.norm(vec)), 1e-9)


class Gallery:
    """Enrolled people: name → embeddings. sqlite, check_same_thread off (pipeline threads)."""

    def __init__(self, path: Path | str) -> None:
        self._path = str(path)
        if self._path != ":memory:":
            Path(self._path).parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(self._path, check_same_thread=False)
        self._conn.execute("CREATE TABLE IF NOT EXISTS people (id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL UNIQUE, enrolled_at TEXT NOT NULL)")
        self._conn.execute("CREATE TABLE IF NOT EXISTS embeddings (id INTEGER PRIMARY KEY AUTOINCREMENT, person_id INTEGER NOT NULL REFERENCES people(id) ON DELETE CASCADE, vec BLOB NOT NULL)")
        self._conn.execute("PRAGMA foreign_keys = ON")
        self._conn.commit()

    def enrol(self, name: str, vectors: list[np.ndarray]) -> int:
        name = name.strip()
        if not name:
            raise ValueError("a name is required")
        row = self._conn.execute("SELECT id FROM people WHERE name = ?", (name,)).fetchone()
        if row is None:
            cur = self._conn.execute("INSERT INTO people (name, enrolled_at) VALUES (?, ?)",
                                     (name, time.strftime("%Y-%m-%dT%H:%M:%S")))
            person_id = int(cur.lastrowid)
        else:
            person_id = int(row[0])
        for vec in vectors:
            self._conn.execute("INSERT INTO embeddings (person_id, vec) VALUES (?, ?)",
                               (person_id, np.asarray(vec, dtype=np.float32).tobytes()))
        self._conn.commit()
        return int(self._conn.execute("SELECT COUNT(*) FROM embeddings WHERE person_id = ?", (person_id,)).fetchone()[0])

    def match(self, vector: np.ndarray, threshold: float) -> tuple[str, float] | None:
        best: tuple[str, float] | None = None
        probe = np.asarray(vector, dtype=np.float32)
        norm = float(np.linalg.norm(probe))
        if norm < 1e-9:
            return None
        probe = probe / norm
        for name, blob in self._conn.execute("SELECT p.name, e.vec FROM embeddings e JOIN people p ON p.id = e.person_id"):
            vec = np.frombuffer(blob, dtype=np.float32)
            score = float(np.dot(probe, vec) / max(float(np.linalg.norm(vec)), 1e-9))
            if score >= threshold and (best is None or score > best[1]):
                best = (name, score)
        return best

    def delete(self, name: str) -> bool:
        cur = self._conn.execute("DELETE FROM people WHERE name = ?", (name.strip(),))
        self._conn.commit()
        return cur.rowcount > 0

    def list(self) -> list[dict]:
        rows = self._conn.execute(
            "SELECT p.name, p.enrolled_at, COUNT(e.id) FROM people p LEFT JOIN embeddings e ON e.person_id = p.id "
            "GROUP BY p.id ORDER BY p.name").fetchall()
        return [{"name": r[0], "enrolled_at": r[1], "samples": int(r[2])} for r in rows]

    def close(self) -> None:
        self._conn.close()


@dataclass(frozen=True)
class FaceMatch:
    box: tuple[float, float, float, float]
    name: str | None
    score: float


class FaceIdentifier:
    def __init__(self, detector: FaceDetector, embedder: FaceEmbedder, gallery: Gallery, *, threshold: float = 0.45) -> None:
        self._detector = detector
        self._embedder = embedder
        self._gallery = gallery
        self.threshold = threshold

    def identify(self, rgb: np.ndarray) -> list[FaceMatch]:
        matches = []
        for face in self._detector.detect(rgb):
            vec = self._embedder.embed(rgb, face.box)
            hit = self._gallery.match(vec, self.threshold)
            matches.append(FaceMatch(box=face.box, name=hit[0] if hit else None, score=hit[1] if hit else 0.0))
        return matches
```

- [ ] **Step 4: Run the tests to verify they pass, then commit**

Run: `.venv/bin/python -m pytest tests/test_perception_faces.py tests/test_perception_image.py -q`

```bash
git add src/richard/perception/image.py src/richard/perception/faces.py tests/test_perception_faces.py
git commit -m "perception: face detector, embedder, local gallery, identifier (opt-in)

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 7: Events, presence state, presence log

**Files:**
- Create: `src/richard/perception/events.py`
- Test: `tests/test_perception_events.py`

**Interfaces:**
- Produces: `KINDS` (frozenset of the seven kinds); `PerceptionEvent(ts: float, source_id: str, kind: str, subject: str = "", confidence: float = 1.0, box=None)` with `to_dict()` and `line() -> str` (e.g. `"Matteo entered (browser)"`); `PresentPerson(subject, since)`; `PresenceState(source_id, *, enter_debounce_s=2.0, leave_debounce_s=10.0)` with `observe(ts, persons: list[Detection], names: list[str | None]) -> list[PerceptionEvent]` and `present() -> list[PresentPerson]`; `PresenceLog(path)` with `append(event, thumbnail: bytes | None = None) -> int`, `recent(since_id=0, limit=100) -> list[dict]`, `last_seen(subject) -> dict | None`, `close()`.
- Consumes: `Detection` from Task 5.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_perception_events.py`:

```python
from richard.perception.detect import Detection
from richard.perception.events import KINDS, PerceptionEvent, PresenceLog, PresenceState

BOX = Detection(box=(0.3, 0.2, 0.6, 0.9), score=0.9)


def kinds(events):
    return [(e.kind, e.subject) for e in events]


def test_event_line_and_dict():
    e = PerceptionEvent(ts=12.5, source_id="browser", kind="identified", subject="matteo", confidence=0.91)
    assert e.line() == "matteo recognised (browser)"
    assert PerceptionEvent(1.0, "browser", "person_entered", "unknown").line() == "someone entered (browser)"
    assert PerceptionEvent(1.0, "browser", "person_left", "matteo").line() == "matteo left (browser)"
    assert PerceptionEvent(1.0, "browser", "stillness", "12.0").line() == "nothing has moved for 12.0 minutes (browser)"
    assert e.to_dict()["kind"] == "identified" and e.to_dict()["confidence"] == 0.91
    assert e.kind in KINDS


def test_someone_must_persist_before_entering():
    state = PresenceState("browser", enter_debounce_s=2.0, leave_debounce_s=10.0)
    assert state.observe(0.0, [BOX], [None]) == []
    assert state.observe(1.0, [BOX], [None]) == []
    events = state.observe(2.5, [BOX], [None])
    assert kinds(events) == [("person_entered", "unknown")]
    assert [p.subject for p in state.present()] == ["unknown"]
    assert state.present()[0].since == 0.0
    assert state.observe(3.0, [BOX], [None]) == []  # no repeats


def test_identity_needs_two_agreeing_matches_then_names_the_presence():
    state = PresenceState("browser", enter_debounce_s=0.0)
    state.observe(0.0, [BOX], [None])
    assert kinds(state.observe(0.5, [BOX], ["matteo"])) == []
    events = state.observe(1.0, [BOX], ["matteo"])
    assert kinds(events) == [("identified", "matteo")]
    assert [p.subject for p in state.present()] == ["matteo"]
    assert state.observe(1.5, [BOX], ["matteo"]) == []


def test_unknown_person_is_reported_once_after_identity_fails_for_a_while():
    state = PresenceState("browser", enter_debounce_s=0.0, unknown_after_s=5.0)
    state.observe(0.0, [BOX], [None])
    for t in (1.0, 3.0):
        assert state.observe(t, [BOX], [None]) == []
    assert kinds(state.observe(5.5, [BOX], [None])) == [("unknown_person", "unknown")]
    assert state.observe(7.0, [BOX], [None]) == []


def test_leaving_needs_a_quiet_gap():
    state = PresenceState("browser", enter_debounce_s=0.0, leave_debounce_s=10.0)
    state.observe(0.0, [BOX], ["matteo"])
    state.observe(0.5, [BOX], ["matteo"])
    assert state.observe(5.0, [], []) == []       # briefly out of frame
    assert kinds(state.observe(8.0, [BOX], ["matteo"])) == []  # back: no re-entry event
    assert state.observe(15.0, [], []) == []          # 7 s gone: not yet
    events = state.observe(20.0, [], [])              # 12 s gone: left
    assert kinds(events) == [("person_left", "matteo")]
    assert state.present() == []


def test_presence_log_round_trip(tmp_path):
    log = PresenceLog(tmp_path / "perception.db")
    e1 = PerceptionEvent(1.0, "browser", "person_entered", "unknown")
    e2 = PerceptionEvent(2.0, "browser", "identified", "matteo", 0.9)
    assert log.append(e1) == 1 and log.append(e2, thumbnail=b"\xff\xd8") == 2
    rows = log.recent()
    assert [r["kind"] for r in rows] == ["person_entered", "identified"]
    assert rows[1]["has_thumbnail"] is True and rows[0]["has_thumbnail"] is False
    assert log.recent(since_id=1) == [rows[1]]
    assert log.last_seen("matteo")["id"] == 2 and log.last_seen("nobody") is None
    assert log.thumbnail(2) == b"\xff\xd8"
    log.close()
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_perception_events.py -q`
Expected: FAIL, `ModuleNotFoundError`.

- [ ] **Step 3: Implement**

Create `src/richard/perception/events.py`:

```python
"""What the sensor says, in words the rest of Richard can use.

PresenceState turns per-frame detections into transitions with debounce: someone
must persist to have entered, be gone for a while to have left, and be recognised
twice to be named. The log keeps the episodes with timestamps; spec two turns them
into memories with provenance "observed".
"""
from __future__ import annotations

import sqlite3
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from richard.perception.detect import Detection

KINDS = frozenset({
    "person_entered", "person_left", "identified", "unknown_person",
    "motion_after_stillness", "scene_changed", "stillness",
})


@dataclass(frozen=True)
class PerceptionEvent:
    ts: float
    source_id: str
    kind: str
    subject: str = ""
    confidence: float = 1.0
    box: tuple[float, float, float, float] | None = None

    def to_dict(self) -> dict:
        return {"ts": self.ts, "source": self.source_id, "kind": self.kind, "subject": self.subject,
                "confidence": self.confidence, "box": list(self.box) if self.box else None}

    def line(self) -> str:
        who = "someone" if self.subject in ("", "unknown") else self.subject
        text = {
            "person_entered": f"{who} entered",
            "person_left": f"{who} left",
            "identified": f"{self.subject} recognised",
            "unknown_person": "an unknown person is here",
            "motion_after_stillness": "movement after a long stillness",
            "scene_changed": "the scene changed",
            "stillness": f"nothing has moved for {self.subject} minutes",
        }.get(self.kind, self.kind)
        return f"{text} ({self.source_id})"


@dataclass
class PresentPerson:
    subject: str
    since: float
    last_seen: float = 0.0
    votes: dict = field(default_factory=dict)
    unknown_reported: bool = False


class PresenceState:
    """One source's people, as a debounced state machine."""

    def __init__(self, source_id: str, *, enter_debounce_s: float = 2.0, leave_debounce_s: float = 10.0,
                 unknown_after_s: float = 20.0) -> None:
        self.source_id = source_id
        self._enter = enter_debounce_s
        self._leave = leave_debounce_s
        self._unknown_after = unknown_after_s
        self._candidate_since: float | None = None
        self._person: PresentPerson | None = None  # one presence slot: "someone is here", named or not
        self._last_seen_any: float | None = None

    def present(self) -> list[PresentPerson]:
        return [self._person] if self._person is not None else []

    def observe(self, ts: float, persons: list[Detection], names: list[str | None]) -> list[PerceptionEvent]:
        events: list[PerceptionEvent] = []
        seen = bool(persons)
        known = [n for n in names if n]
        if seen:
            self._last_seen_any = ts
        if self._person is None:
            if not seen:
                self._candidate_since = None
                return events
            if self._candidate_since is None:
                self._candidate_since = ts
            if ts - self._candidate_since < self._enter:
                return events
            self._person = PresentPerson(subject="unknown", since=self._candidate_since, last_seen=ts)
            events.append(PerceptionEvent(ts, self.source_id, "person_entered", "unknown", persons[0].score, persons[0].box))
        person = self._person
        if seen:
            person.last_seen = ts
            for name in known:
                person.votes[name] = person.votes.get(name, 0) + 1
                if person.votes[name] >= 2 and person.subject != name:
                    person.subject = name
                    events.append(PerceptionEvent(ts, self.source_id, "identified", name, 0.9, persons[0].box))
            if person.subject == "unknown" and not person.unknown_reported and ts - person.since >= self._unknown_after:
                person.unknown_reported = True
                events.append(PerceptionEvent(ts, self.source_id, "unknown_person", "unknown", persons[0].score, persons[0].box))
            return events
        if ts - person.last_seen >= self._leave:
            events.append(PerceptionEvent(ts, self.source_id, "person_left", person.subject))
            self._person = None
            self._candidate_since = None
        return events


class PresenceLog:
    def __init__(self, path: Path | str) -> None:
        self._path = str(path)
        if self._path != ":memory:":
            Path(self._path).parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(self._path, check_same_thread=False)
        self._conn.execute(
            "CREATE TABLE IF NOT EXISTS perception_log (id INTEGER PRIMARY KEY AUTOINCREMENT, at TEXT NOT NULL, "
            "ts REAL NOT NULL, source TEXT NOT NULL, kind TEXT NOT NULL, subject TEXT NOT NULL, "
            "confidence REAL NOT NULL, thumbnail BLOB)")
        self._conn.commit()

    def append(self, event: PerceptionEvent, thumbnail: bytes | None = None) -> int:
        cur = self._conn.execute(
            "INSERT INTO perception_log (at, ts, source, kind, subject, confidence, thumbnail) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (datetime.now(timezone.utc).isoformat(timespec="seconds"), event.ts, event.source_id, event.kind,
             event.subject, event.confidence, thumbnail))
        self._conn.commit()
        return int(cur.lastrowid)

    @staticmethod
    def _row(r) -> dict:
        return {"id": r[0], "at": r[1], "ts": r[2], "source": r[3], "kind": r[4], "subject": r[5],
                "confidence": r[6], "has_thumbnail": r[7] is not None}

    def recent(self, since_id: int = 0, limit: int = 100) -> list[dict]:
        rows = self._conn.execute(
            "SELECT id, at, ts, source, kind, subject, confidence, thumbnail FROM perception_log "
            "WHERE id > ? ORDER BY id DESC LIMIT ?", (since_id, limit)).fetchall()
        return [self._row(r) for r in reversed(rows)]

    def last_seen(self, subject: str) -> dict | None:
        r = self._conn.execute(
            "SELECT id, at, ts, source, kind, subject, confidence, thumbnail FROM perception_log "
            "WHERE subject = ? AND kind IN ('identified', 'person_entered', 'person_left') ORDER BY id DESC LIMIT 1",
            (subject,)).fetchone()
        return self._row(r) if r else None

    def thumbnail(self, event_id: int) -> bytes | None:
        r = self._conn.execute("SELECT thumbnail FROM perception_log WHERE id = ?", (event_id,)).fetchone()
        return r[0] if r else None

    def close(self) -> None:
        self._conn.close()
```

- [ ] **Step 4: Run the tests to verify they pass, then commit**

Run: `.venv/bin/python -m pytest tests/test_perception_events.py -q`

```bash
git add src/richard/perception/events.py tests/test_perception_events.py
git commit -m "perception: events, debounced presence state, presence log

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 8: The gate

**Files:**
- Create: `src/richard/perception/gate.py`
- Test: `tests/test_perception_gate.py`

**Interfaces:**
- Produces: `GatePolicy(enabled=True, quiet_hours="", cooldown_s=120.0)`; `parse_quiet_hours(text) -> tuple[tuple[int, int], tuple[int, int]] | None` (`"23:00-07:30"` → `((23, 0), (7, 30))`, `""` → None, bad text → `ValueError`); `in_quiet_hours(policy, now: datetime) -> bool`; `Gate(policy, *, clock=time.monotonic, wall=datetime.now)` with `admit(events: list[PerceptionEvent]) -> list[PerceptionEvent]` and `reasons: list[tuple[str, str]]` of the last drops (`(kind, reason)`).
- Consumes: `PerceptionEvent` (Task 7).

- [ ] **Step 1: Write the failing tests**

Create `tests/test_perception_gate.py`:

```python
from datetime import datetime

import pytest

from richard.perception.events import PerceptionEvent
from richard.perception.gate import Gate, GatePolicy, in_quiet_hours, parse_quiet_hours


def ev(kind, subject="unknown", ts=0.0):
    return PerceptionEvent(ts, "browser", kind, subject)


class Clock:
    def __init__(self, t=0.0):
        self.t = t

    def __call__(self):
        return self.t


def test_parse_quiet_hours():
    assert parse_quiet_hours("") is None
    assert parse_quiet_hours("23:00-07:30") == ((23, 0), (7, 30))
    with pytest.raises(ValueError):
        parse_quiet_hours("late-early")


@pytest.mark.parametrize("text,hour,minute,quiet", [
    ("23:00-07:30", 23, 30, True), ("23:00-07:30", 3, 0, True), ("23:00-07:30", 7, 29, True),
    ("23:00-07:30", 7, 30, False), ("23:00-07:30", 12, 0, False),
    ("13:00-14:00", 13, 30, True), ("13:00-14:00", 14, 0, False), ("", 3, 0, False),
])
def test_in_quiet_hours(text, hour, minute, quiet):
    policy = GatePolicy(quiet_hours=text)
    assert in_quiet_hours(policy, datetime(2026, 9, 9, hour, minute)) is quiet


def test_disabled_gate_admits_nothing():
    gate = Gate(GatePolicy(enabled=False))
    assert gate.admit([ev("person_entered")]) == []
    assert gate.reasons == [("person_entered", "disabled")]


def test_cooldown_per_kind_and_subject():
    clock = Clock(0.0)
    gate = Gate(GatePolicy(cooldown_s=120.0), clock=clock, wall=lambda: datetime(2026, 9, 9, 12, 0))
    assert gate.admit([ev("identified", "matteo")]) == [ev("identified", "matteo")]
    clock.t = 60.0
    assert gate.admit([ev("identified", "matteo")]) == []
    assert gate.reasons == [("identified", "cooldown")]
    assert gate.admit([ev("identified", "guest")]) == [ev("identified", "guest")]  # other subject
    clock.t = 121.0
    assert gate.admit([ev("identified", "matteo")]) == [ev("identified", "matteo")]


def test_quiet_hours_drop_everything_but_still_update_nothing():
    gate = Gate(GatePolicy(quiet_hours="23:00-07:30"), wall=lambda: datetime(2026, 9, 9, 2, 0))
    assert gate.admit([ev("person_entered"), ev("scene_changed")]) == []
    assert gate.reasons == [("person_entered", "quiet_hours"), ("scene_changed", "quiet_hours")]
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_perception_gate.py -q`
Expected: FAIL, `ModuleNotFoundError`.

- [ ] **Step 3: Implement**

Create `src/richard/perception/gate.py`:

```python
"""The gate filters repetition, not relevance.

Debounce and "transitions only" already happened in PresenceState and the motion
stage; here: a master switch, quiet hours, and a cooldown per (kind, subject) so a
person pacing in and out of frame does not wake the brain every time. Relevance
(is this worth a word?) belongs to the brain and, later, spec four's policy.
"""
from __future__ import annotations

import time
from dataclasses import dataclass
from datetime import datetime

from richard.perception.events import PerceptionEvent


@dataclass
class GatePolicy:
    enabled: bool = True
    quiet_hours: str = ""
    cooldown_s: float = 120.0


def parse_quiet_hours(text: str):
    text = (text or "").strip()
    if not text:
        return None
    try:
        start, end = text.split("-")
        sh, sm = (int(v) for v in start.split(":"))
        eh, em = (int(v) for v in end.split(":"))
    except ValueError as exc:
        raise ValueError("quiet_hours must look like 23:00-07:30") from exc
    for h, m in ((sh, sm), (eh, em)):
        if not (0 <= h < 24 and 0 <= m < 60):
            raise ValueError("quiet_hours must look like 23:00-07:30")
    return ((sh, sm), (eh, em))


def in_quiet_hours(policy: GatePolicy, now: datetime) -> bool:
    window = parse_quiet_hours(policy.quiet_hours)
    if window is None:
        return False
    (sh, sm), (eh, em) = window
    minute = now.hour * 60 + now.minute
    start, end = sh * 60 + sm, eh * 60 + em
    if start <= end:
        return start <= minute < end
    return minute >= start or minute < end  # wraps midnight


class Gate:
    def __init__(self, policy: GatePolicy, *, clock=time.monotonic, wall=datetime.now) -> None:
        self.policy = policy
        self._clock = clock
        self._wall = wall
        self._last: dict[tuple[str, str], float] = {}
        self.reasons: list[tuple[str, str]] = []

    def admit(self, events: list[PerceptionEvent]) -> list[PerceptionEvent]:
        self.reasons = []
        admitted: list[PerceptionEvent] = []
        now = self._clock()
        quiet = in_quiet_hours(self.policy, self._wall())
        for event in events:
            if not self.policy.enabled:
                self.reasons.append((event.kind, "disabled"))
                continue
            if quiet:
                self.reasons.append((event.kind, "quiet_hours"))
                continue
            key = (event.kind, event.subject)
            last = self._last.get(key)
            if last is not None and now - last < self.policy.cooldown_s:
                self.reasons.append((event.kind, "cooldown"))
                continue
            self._last[key] = now
            admitted.append(event)
        return admitted
```

- [ ] **Step 4: Run the tests to verify they pass, then commit**

Run: `.venv/bin/python -m pytest tests/test_perception_gate.py -q`

```bash
git add src/richard/perception/gate.py tests/test_perception_gate.py
git commit -m "perception: the gate (switch, quiet hours, cooldown per kind and subject)

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 9: Pipeline and service

**Files:**
- Create: `src/richard/perception/pipeline.py`
- Test: `tests/test_perception_pipeline.py`

**Interfaces:**
- Produces: `Settings` (dataclass built from the `[plugins.perception]` table via `Settings.from_table(table: dict)`; fields: `identity_enabled`, `quiet_hours`, `sensitivity`, `cooldown_s`, `enter_debounce_s`, `leave_debounce_s`, `stream_fps`, `keep_thumbnails`, `device`, `stale_s`; derived `motion_threshold` and `person_threshold`); `SourcePipeline(source_id, hub, *, settings, person_detector=None, identifier=None, clock)` with `step() -> list[PerceptionEvent]` (raw, ungated) and `.presence: PresenceState`; `PerceptionService(settings, *, data_dir, clock=time.monotonic, wall=datetime.now, person_detector=None, identifier=None, write=print)` with `push_frame(source_id, jpeg) -> None`, `remove_source(source_id)`, `live_sources() -> list[str]`, `start()`, `stop()`, `add_sink(fn)`, `event_source(sink) -> stop` (plugin contract), `set_context_sink(fn)` where `fn(line: str) -> bool`, `process(source_id) -> list[PerceptionEvent]` (one synchronous step through the gate and sinks, for tests and replays), `status() -> dict`, `snapshot(source_id=None, detail="low", region=None) -> tuple[bytes, dict] | None`, `recent_events(since_id=0, limit=100)`, `presence() -> list[dict]`, `enrol(name, jpegs: list[bytes]) -> int`, `gallery`, `log`.
- Sensitivity mapping: `motion_threshold = 0.02 + (100 - sensitivity) / 100 * 0.08`; `person_threshold = 0.4 + (100 - sensitivity) / 100 * 0.3`.
- Event → plugin `Event`: `Event(kind=event.kind, target=f"perception:{target}", payload=event.to_dict(), observed_at=now)` with target `person_present` for `person_entered`/`person_left`, `identified:<name>` for `identified`, otherwise the kind.
- Context routing: an admitted event is first offered to the context sink (`fn(event.line())`); when it returns `True` (a session took it) it is logged but NOT pushed to the loop sinks; when `False` or no sink, it goes to the loop sinks. The log gets every admitted event either way.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_perception_pipeline.py`:

```python
import io

import numpy as np
import pytest

pytest.importorskip("PIL")
from PIL import Image  # noqa: E402

from richard.perception.detect import Detection  # noqa: E402
from richard.perception.faces import FaceMatch  # noqa: E402
from richard.perception.pipeline import PerceptionService, Settings  # noqa: E402


def _jpeg(color, w=64, h=36):
    buf = io.BytesIO()
    Image.new("RGB", (w, h), color).save(buf, format="JPEG")
    return buf.getvalue()


class Clock:
    def __init__(self, t=1000.0):
        self.t = t

    def __call__(self):
        return self.t


class ScriptedPersons:
    """Returns the next scripted detection list on each call; sticks on the last."""

    def __init__(self, script):
        self.script = list(script)
        self.calls = 0

    def detect(self, rgb):
        self.calls += 1
        if len(self.script) > 1:
            return self.script.pop(0)
        return self.script[0] if self.script else []


class ScriptedFaces:
    def __init__(self, name):
        self.name = name

    def identify(self, rgb):
        return [FaceMatch(box=(0.4, 0.2, 0.6, 0.5), name=self.name, score=0.9)]


PERSON = [Detection(box=(0.3, 0.1, 0.7, 0.9), score=0.9)]


def test_settings_from_table_and_thresholds():
    s = Settings.from_table({"sensitivity": 50, "quiet_hours": "23:00-07:30", "stream_fps": 4})
    assert s.motion_threshold == pytest.approx(0.06) and s.person_threshold == pytest.approx(0.55)
    assert s.stream_fps == 4 and s.quiet_hours == "23:00-07:30" and s.identity_enabled is False
    assert Settings.from_table({}).cooldown_s == 120.0


def test_service_emits_entered_identified_left_through_gate_and_sinks(tmp_path):
    clock = Clock()
    settings = Settings.from_table({"enter_debounce_s": 1.0, "leave_debounce_s": 3.0, "cooldown_s": 0.0, "identity_enabled": True})
    svc = PerceptionService(settings, data_dir=tmp_path, clock=clock,
                            person_detector=ScriptedPersons([PERSON]), identifier=ScriptedFaces("matteo"))
    seen = []
    svc.add_sink(seen.append)
    for t in (0.0, 0.5, 1.5, 2.5, 3.5):  # face stage runs once per second while unresolved
        clock.t = 1000.0 + t
        svc.push_frame("browser", _jpeg((10, 10, 10)))
        svc.process("browser")
    kinds = [(e.kind, e.subject) for e in seen]
    assert kinds[:2] == [("person_entered", "unknown"), ("identified", "matteo")]
    assert svc.presence() == [{"source": "browser", "subject": "matteo", "since": 1000.0}]
    svc._pipelines["browser"]._person_detector = ScriptedPersons([[]])
    for t in (4.0, 5.0, 8.0):
        clock.t = 1000.0 + t
        svc.push_frame("browser", _jpeg((10, 10, 10)))
        svc.process("browser")
    assert [(e.kind, e.subject) for e in seen][-1] == ("person_left", "matteo")
    assert [r["kind"] for r in svc.recent_events()] == ["person_entered", "identified", "person_left"]
    assert svc.log.last_seen("matteo")["kind"] == "person_left"


def test_context_sink_takes_events_instead_of_loop_sinks(tmp_path):
    clock = Clock()
    settings = Settings.from_table({"enter_debounce_s": 0.0, "cooldown_s": 0.0})
    svc = PerceptionService(settings, data_dir=tmp_path, clock=clock, person_detector=ScriptedPersons([PERSON]))
    loop_events = []
    lines = []
    stop = svc.event_source(loop_events.append)
    svc.set_context_sink(lambda line: lines.append(line) or True)
    svc.push_frame("browser", _jpeg((0, 0, 0)))
    svc.process("browser")
    assert lines == ["someone entered (browser)"]
    assert loop_events == []
    svc.set_context_sink(lambda line: False)
    svc._pipelines["browser"]._person_detector = ScriptedPersons([[]])
    clock.t = 1020.0
    svc.push_frame("browser", _jpeg((0, 0, 0)))
    svc.process("browser")
    assert [e.kind for e in loop_events] == ["person_left"]
    assert loop_events[0].target == "perception:person_present"
    stop()


def test_snapshot_detail_and_region(tmp_path):
    svc = PerceptionService(Settings.from_table({}), data_dir=tmp_path, person_detector=ScriptedPersons([[]]))
    assert svc.snapshot() is None
    svc.push_frame("browser", _jpeg((200, 30, 30), w=1600, h=900))
    jpeg, meta = svc.snapshot(detail="low")
    assert meta["width"] == 800 and meta["height"] == 450 and meta["source"] == "browser"
    _, meta_high = svc.snapshot(detail="high")
    assert meta_high["width"] == 1600
    _, meta_region = svc.snapshot(region="left")  # 800x900 crop, bounded to an 800 px long edge
    assert meta_region["width"] == 711 and meta_region["height"] == 800 and meta_region["region"] == "left"
    with pytest.raises(ValueError):
        svc.snapshot(region="nowhere")


def test_status_reports_sources_and_staleness(tmp_path):
    clock = Clock()
    svc = PerceptionService(Settings.from_table({"stale_s": 5}), data_dir=tmp_path, clock=clock, person_detector=ScriptedPersons([[]]))
    assert svc.status()["sources"] == []
    svc.push_frame("browser", _jpeg((0, 0, 0)))
    assert svc.status()["sources"] == [{"id": "browser", "stale": False, "frames": 1}]
    clock.t += 10
    assert svc.status()["sources"][0]["stale"] is True
    assert svc.live_sources() == []


def test_enrol_uses_the_identifier_and_the_gallery(tmp_path):
    class Enroller:
        def identify(self, rgb):
            return [FaceMatch(box=(0.1, 0.1, 0.5, 0.5), name=None, score=0.0)]

        def embed_box(self, rgb, box):
            v = np.zeros(512, dtype=np.float32); v[0] = 1.0
            return v

    svc = PerceptionService(Settings.from_table({"identity_enabled": True}), data_dir=tmp_path,
                            person_detector=ScriptedPersons([[]]), identifier=Enroller())
    assert svc.enrol("matteo", [_jpeg((1, 2, 3)), _jpeg((3, 2, 1))]) == 2
    assert [p["name"] for p in svc.gallery.list()] == ["matteo"]
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_perception_pipeline.py -q`
Expected: FAIL, `ModuleNotFoundError`.

- [ ] **Step 3: Implement**

Create `src/richard/perception/pipeline.py`:

```python
"""One pipeline per frame source, one service for all of them.

SourcePipeline.step(): pull the latest frame (skip if unchanged), motion stage,
person stage, face stage (opt-in, only while someone unnamed is present, every few
seconds), presence state → raw events. PerceptionService: hub, pipelines, gate,
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
                setattr(s, key, type(current)(value) if not isinstance(current, bool) else bool(value))
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
        self._was_still_for: float = 0.0
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
```

Then add to `FaceIdentifier` in `src/richard/perception/faces.py` the one method the service's enrolment uses:

```python
    def embed_box(self, rgb, box):
        return self._embedder.embed(rgb, box)
```

- [ ] **Step 4: Run the tests to verify they pass, then commit**

Run: `.venv/bin/python -m pytest tests/test_perception_pipeline.py tests/test_perception_faces.py -q`

```bash
git add src/richard/perception/pipeline.py src/richard/perception/faces.py tests/test_perception_pipeline.py
git commit -m "perception: per-source pipeline and the service (hub, gate, log, sinks, snapshots, enrolment)

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 10: Engine: tool results that carry images

**Files:**
- Modify: `src/richard/providers/base.py`, `src/richard/engine.py`
- Test: `tests/test_engine_streaming.py`, `tests/test_engine.py`

**Interfaces:**
- Produces: `richard.providers.base.ToolResult(text: str, images: tuple[str, ...] = ())` (frozen dataclass); `Provider.execute` may return `str | ToolResult`; the engine appends the tool message with `result.text`, then, after all tool messages of the round, ONE user message `user_parts(None, images)` with every image of the round, to both the working request and the conversation. This is the app's own sequence (tool result, then image as a user message), so the history has one shape whichever side took the picture.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_engine_streaming.py`:

```python
from richard.providers.base import ToolResult  # noqa: E402

IMG_A = "data:image/jpeg;base64,/9j/AAAA"


class CameraProviderFake:
    def schemas(self):
        return [{"type": "function", "function": {"name": "camera", "parameters": {}}}]

    def execute(self, name, arguments):
        return ToolResult(text='{"image_attached": true}', images=(IMG_A,))

    def context(self):
        return None


def test_tool_result_with_images_becomes_tool_message_then_user_image_message():
    brain = FakeBrain([
        {"tool_calls": [ToolCall(id="c1", name="camera", arguments={"question": "q"})]},
        {"deltas": ["A mug."]},
    ])
    engine = Engine(brain, [CameraProviderFake()], Personality())
    convo = Conversation()
    convo.add_user("look")
    assert "".join(engine.respond_streaming(convo)) == "A mug."
    served = brain.calls[1]
    assert [m["role"] for m in served] == ["system", "user", "assistant", "tool", "user"]
    assert served[3] == {"role": "tool", "tool_call_id": "c1", "content": '{"image_attached": true}'}
    assert served[4]["content"] == [{"type": "image_url", "image_url": {"url": IMG_A}}]
    assert [m.role for m in convo.history()] == ["user", "assistant", "tool", "user", "assistant"]
    assert convo.pending_client_calls() == []
```

Append to `tests/test_engine.py`:

```python
def test_respond_handles_tool_results_with_images():
    from richard.brain.completion import ToolCall
    from richard.providers.base import ToolResult

    class Cam:
        def schemas(self):
            return [{"type": "function", "function": {"name": "camera", "parameters": {}}}]

        def execute(self, name, arguments):
            return ToolResult(text="ok", images=("data:image/jpeg;base64,/9j/AAAA",))

        def context(self):
            return None

    brain = FakeBrain([
        Completion(content=None, tool_calls=[ToolCall(id="1", name="camera", arguments={})]),
        Completion(content="A mug.", tool_calls=[]),
    ])
    engine = Engine(brain, [Cam()], Personality())
    assert engine.respond(Conversation()) == "A mug."
    served = brain.calls[1][0]
    assert [m["role"] for m in served] == ["system", "assistant", "tool", "user"]
    assert served[3]["content"][0]["type"] == "image_url"
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_engine_streaming.py tests/test_engine.py -q`
Expected: FAIL, `ImportError: cannot import name 'ToolResult'`.

- [ ] **Step 3: Implement**

In `src/richard/providers/base.py` add:

```python
from dataclasses import dataclass


@dataclass(frozen=True)
class ToolResult:
    """A tool result that carries pictures. The engine serves `text` as the tool message
    and the images as a user message right after it (the Reachy app's own sequence)."""

    text: str
    images: tuple[str, ...] = ()  # data URLs
```

and change the `execute` docstring type to `-> "str | ToolResult"`.

In `src/richard/engine.py`: import `from richard.conversation import Conversation, user_parts` and `from richard.providers.base import Provider, ToolResult`; change `_execute` to return `str | ToolResult` (pass a `ToolResult` through unchanged, wrap exceptions as today), and rewrite the loop in `_record_tool_round`:

```python
        images: list[str] = []
        for call in completion.tool_calls:
            if call.id in deferred:
                continue
            result = self._execute(call.name, call.arguments)
            text = result.text if isinstance(result, ToolResult) else result
            if isinstance(result, ToolResult):
                images.extend(result.images)
            working.append({"role": "tool", "tool_call_id": call.id, "content": text})
            conversation.add_tool_result(call.id, text)
        if images:
            parts = user_parts(None, images)
            working.append({"role": "user", "content": parts})
            conversation.add_user(parts)
```

- [ ] **Step 4: Run the tests to verify they pass, then commit**

Run: `.venv/bin/python -m pytest tests/test_engine_streaming.py tests/test_engine.py tests/test_realtime_session.py -q`

```bash
git add src/richard/providers/base.py src/richard/engine.py tests/test_engine_streaming.py tests/test_engine.py
git commit -m "engine: ToolResult with images, served as a user image message after the tool message

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 11: Camera and presence providers

**Files:**
- Create: `src/richard/perception/camera.py`
- Test: `tests/test_perception_camera.py`

**Interfaces:**
- Produces: `CameraProvider(service)` with `schemas()` (the `camera` tool with `question` required, `detail` enum `low|high`, `region` string; EMPTY when `service.live_sources()` is empty), `execute("camera", args) -> ToolResult | str`, `context() -> None`; `PresenceProvider(service)` with tools `who_is_here()` and `last_seen(name)` returning short strings.
- Consumes: `PerceptionService.snapshot/live_sources/presence/log`, `image.data_url`, `ToolResult`.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_perception_camera.py`:

```python
import json

from richard.perception.camera import CameraProvider, PresenceProvider
from richard.providers.base import ToolResult


class FakeService:
    def __init__(self, live=("browser",)):
        self._live = list(live)
        self.calls = []
        self._presence = [{"source": "browser", "subject": "matteo", "since": 5.0}]

        class Log:
            def last_seen(self, name):
                return {"at": "2026-09-09T18:00:00+00:00", "kind": "person_left", "source": "browser"} if name == "matteo" else None
        self.log = Log()

    def live_sources(self):
        return self._live

    def snapshot(self, source_id=None, detail="low", region=None):
        self.calls.append((source_id, detail, region))
        if region == "nowhere":
            raise ValueError("region must be left|right|centre|top|bottom or x0,y0,x1,y1")
        return b"\xff\xd8jpeg", {"source": "browser", "detail": detail, "region": region, "width": 800, "height": 450}

    def presence(self):
        return self._presence


def test_camera_schema_only_when_a_source_is_live():
    assert CameraProvider(FakeService(live=())).schemas() == []
    (schema,) = CameraProvider(FakeService()).schemas()
    fn = schema["function"]
    assert fn["name"] == "camera" and fn["parameters"]["required"] == ["question"]
    assert fn["parameters"]["properties"]["detail"]["enum"] == ["low", "high"]
    assert "region" in fn["parameters"]["properties"]


def test_camera_execute_returns_the_frame_as_a_tool_result():
    svc = FakeService()
    out = CameraProvider(svc).execute("camera", {"question": "what", "detail": "high", "region": "left"})
    assert isinstance(out, ToolResult)
    assert json.loads(out.text) == {"image_attached": True, "image_width": 800, "image_height": 450, "source": "browser", "detail": "high", "region": "left"}
    assert out.images == ("data:image/jpeg;base64,/9jqcGVn",)
    assert svc.calls == [(None, "high", "left")]


def test_camera_execute_reports_no_frame_and_bad_region_as_text():
    assert "No camera is streaming" in CameraProvider(FakeService(live=())).execute("camera", {"question": "q"})
    out = CameraProvider(FakeService()).execute("camera", {"question": "q", "region": "nowhere"})
    assert isinstance(out, str) and "region" in out


def test_presence_tools():
    p = PresenceProvider(FakeService())
    assert [s["function"]["name"] for s in p.schemas()] == ["who_is_here", "last_seen"]
    assert p.execute("who_is_here", {}) == "Present now: matteo (browser)."
    assert "matteo" in p.execute("last_seen", {"name": "matteo"}) and "2026-09-09" in p.execute("last_seen", {"name": "matteo"})
    assert "never" in p.execute("last_seen", {"name": "ghost"}).lower()
    svc = FakeService()
    svc._presence = []
    assert PresenceProvider(svc).execute("who_is_here", {}) == "Nobody is in view right now."
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_perception_camera.py -q`
Expected: FAIL, `ModuleNotFoundError`.

- [ ] **Step 3: Implement**

Create `src/richard/perception/camera.py`:

```python
"""The brain's own eyes on the ambient stream: `camera` served from the latest frame,
plus two presence questions. When no source is live the camera schema is withheld,
so a connected Reachy app's client-side `camera` (spec 3a) is used instead."""
from __future__ import annotations

import json

from richard.perception import image
from richard.providers.base import ToolResult

CAMERA_SCHEMA = {
    "type": "function",
    "function": {
        "name": "camera",
        "description": (
            "Take a picture from the live camera to see what is in front of you: who is here, what "
            "someone is holding, the room. Use it when asked to look, or when an event you were told "
            "about deserves a closer look. Each call captures the current moment. Start with detail "
            "'low'; use 'high' only to read text or small objects; use 'region' (left, right, centre, "
            "top, bottom, or x0,y0,x1,y1 in 0..1) to look closely at one part of the view."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "question": {"type": "string", "description": "What to observe or ask about in the picture."},
                "detail": {"type": "string", "enum": ["low", "high"], "description": "low (default) or high."},
                "region": {"type": "string", "description": "Optional part of the view to crop at full resolution."},
            },
            "required": ["question"],
        },
    },
}

WHO_SCHEMA = {"type": "function", "function": {
    "name": "who_is_here", "description": "Who the camera currently sees, and since when.",
    "parameters": {"type": "object", "properties": {}}}}
LAST_SEEN_SCHEMA = {"type": "function", "function": {
    "name": "last_seen", "description": "When a named person was last seen by the camera.",
    "parameters": {"type": "object", "properties": {"name": {"type": "string"}}, "required": ["name"]}}}


class CameraProvider:
    def __init__(self, service) -> None:
        self._service = service

    def schemas(self) -> list[dict]:
        return [CAMERA_SCHEMA] if self._service.live_sources() else []

    def execute(self, name: str, arguments: dict):
        if name != "camera":
            return f"Unknown tool: {name}."
        detail = "high" if arguments.get("detail") == "high" else "low"
        region = (arguments.get("region") or "").strip() or None
        try:
            shot = self._service.snapshot(detail=detail, region=region)
        except ValueError as exc:
            return f"Cannot take that picture: {exc}"
        if shot is None:
            return "No camera is streaming right now, so I cannot look."
        jpeg, meta = shot
        text = json.dumps({"image_attached": True, "image_width": meta["width"], "image_height": meta["height"],
                           "source": meta["source"], "detail": detail, "region": region})
        return ToolResult(text=text, images=(image.data_url(jpeg),))

    def context(self) -> str | None:
        return None


class PresenceProvider:
    def __init__(self, service) -> None:
        self._service = service

    def schemas(self) -> list[dict]:
        return [WHO_SCHEMA, LAST_SEEN_SCHEMA]

    def execute(self, name: str, arguments: dict) -> str:
        if name == "who_is_here":
            present = self._service.presence()
            if not present:
                return "Nobody is in view right now."
            return "Present now: " + ", ".join(f"{p['subject']} ({p['source']})" for p in present) + "."
        if name == "last_seen":
            who = str(arguments.get("name") or "").strip()
            row = self._service.log.last_seen(who) if who else None
            if row is None:
                return f"{who or 'That person'} has never been seen by the camera."
            return f"{who}: last event '{row['kind']}' at {row['at']} on {row['source']}."
        return f"Unknown tool: {name}."

    def context(self) -> str | None:
        return None
```

- [ ] **Step 4: Run the tests to verify they pass, then commit**

Run: `.venv/bin/python -m pytest tests/test_perception_camera.py -q`

```bash
git add src/richard/perception/camera.py tests/test_perception_camera.py
git commit -m "perception: camera (detail, region) and presence providers

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 12: Target reader and the built-in plugin

**Files:**
- Create: `src/richard/perception/reader.py`, `src/richard/plugins/perception/__init__.py`
- Modify: `src/richard/plugins/registry.py` (add `plugin(name)`)
- Test: `tests/test_plugin_perception.py`

**Interfaces:**
- Produces: `PERCEPTION = "perception"`; `PerceptionTargetReader(service)` (kind `perception`) with `read(target_id) -> {"name", "state", "attributes"}` for `person_present` (state `on|off`, attributes `{"present": [...subjects]}`) and `identified:<name>` (state `on|off`), `list_targets()`, `verify(target_id, expected)`; `PerceptionPlugin(*, detector_factory=None, identifier_factory=None)` with `name = "perception"`, `version = "0.1"`, `config_defaults()`, `build(ctx) -> PluginParts`, and `.service` set after build; `PluginRegistry.plugin(name) -> Plugin | None`.
- Consumes: `PerceptionService`, `Settings`, `CameraProvider`, `PresenceProvider`, `richard.verification.judge/failed/VerificationResult`, `PluginParts`.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_plugin_perception.py`:

```python
from pathlib import Path

import pytest

pytest.importorskip("PIL")

from richard.perception.reader import PerceptionTargetReader  # noqa: E402
from richard.plugins.base import PluginContext, TargetInfo  # noqa: E402
from richard.plugins.perception import PerceptionPlugin  # noqa: E402
from richard.plugins.registry import PluginRegistry  # noqa: E402
from richard.verification import VerificationStatus  # noqa: E402


class FakeService:
    def __init__(self, present=(), gallery=("matteo",)):
        self._present = list(present)
        self._gallery = [{"name": n, "samples": 1, "enrolled_at": "x"} for n in gallery]

        class G:
            def list(inner):
                return self._gallery
        self.gallery = G()

    def presence(self):
        return [{"source": "browser", "subject": s, "since": 1.0} for s in self._present]


def test_reader_lists_and_reads_presence_targets():
    reader = PerceptionTargetReader(FakeService(present=["matteo"]))
    assert reader.list_targets() == [TargetInfo(id="person_present", name="Someone present"),
                                     TargetInfo(id="identified:matteo", name="matteo present")]
    assert reader.read("person_present") == {"name": "Someone present", "state": "on", "attributes": {"present": ["matteo"]}}
    assert reader.read("identified:matteo")["state"] == "on"
    assert reader.read("identified:guest")["state"] == "off"
    with pytest.raises(KeyError):
        reader.read("weather")


def test_reader_verify_confirms_and_mismatches():
    reader = PerceptionTargetReader(FakeService(present=["matteo"]))
    assert reader.verify("person_present", {"state": "on"}).status == VerificationStatus.CONFIRMED
    assert reader.verify("identified:guest", {"state": "on"}).status == VerificationStatus.MISMATCH
    assert reader.verify("nope", {"state": "on"}).status == VerificationStatus.FAILED


def test_plugin_builds_parts_with_injected_detectors(tmp_path):
    class Det:
        def detect(self, rgb):
            return []

    plugin = PerceptionPlugin(detector_factory=lambda settings, write: Det(), identifier_factory=lambda settings, gallery, write: None)
    ctx = PluginContext(config={**plugin.config_defaults(), "sensitivity": 70}, persona_name="Richard",
                        data_dir=tmp_path / "perception", write=lambda s: None)
    parts = plugin.build(ctx)
    names = [s["function"]["name"] for p in parts.providers for s in p.schemas()]
    assert names == ["who_is_here", "last_seen"]  # camera withheld: no live source yet
    assert "perception" in parts.target_readers and len(parts.event_sources) == 1
    assert "perception" in parts.context.lower()
    assert plugin.service.settings.sensitivity == 70 and plugin.service.started is True
    assert Path(tmp_path / "perception" / "perception.db").exists()
    parts.shutdown()
    assert plugin.service.started is False


def test_plugin_defaults_have_the_documented_keys():
    keys = set(PerceptionPlugin().config_defaults())
    assert keys == {"identity_enabled", "quiet_hours", "sensitivity", "cooldown_s", "enter_debounce_s",
                    "leave_debounce_s", "stream_fps", "keep_thumbnails", "device", "stale_s"}


def test_registry_exposes_a_built_plugin_instance(tmp_path):
    plugin = PerceptionPlugin(detector_factory=lambda s, w: None, identifier_factory=lambda s, g, w: None)
    registry = PluginRegistry([plugin])
    assert registry.plugin("perception") is plugin
    assert registry.plugin("nope") is None
    registry.build(["perception"], {}, persona_name="R", data_dir=tmp_path, write=lambda s: None)
    registry.shutdown()
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_plugin_perception.py -q`
Expected: FAIL, `ModuleNotFoundError`.

- [ ] **Step 3: Implement**

Create `src/richard/perception/reader.py`:

```python
"""Presence as control-loop targets (kind "perception"): `perception:person_present`,
`perception:identified:<name>`. Loops can watch them like Home Assistant entities."""
from __future__ import annotations

from richard.plugins.base import TargetInfo
from richard.verification import VerificationResult, failed, judge

PERCEPTION = "perception"
PRESENT = "person_present"


class PerceptionTargetReader:
    def __init__(self, service) -> None:
        self._service = service

    def _snapshot(self, target_id: str) -> dict:
        present = [p["subject"] for p in self._service.presence()]
        if target_id == PRESENT:
            return {"name": "Someone present", "state": "on" if present else "off", "attributes": {"present": present}}
        if target_id.startswith("identified:"):
            name = target_id.split(":", 1)[1]
            return {"name": f"{name} present", "state": "on" if name in present else "off", "attributes": {}}
        raise KeyError(target_id)

    def read(self, target_id: str) -> dict:
        return self._snapshot(target_id)

    def list_targets(self) -> list[TargetInfo]:
        names = [p["name"] for p in self._service.gallery.list()] if self._service.gallery else []
        return [TargetInfo(id=PRESENT, name="Someone present")] + [
            TargetInfo(id=f"identified:{n}", name=f"{n} present") for n in names]

    def verify(self, target_id: str, expected: dict) -> VerificationResult:
        try:
            snap = self._snapshot(target_id)
        except KeyError:
            return failed(PERCEPTION, f"perception:{target_id}", target_id, "verify", "unknown target")

        def build(status, **kwargs):
            return VerificationResult(status=status, source=PERCEPTION, target=f"perception:{target_id}",
                                      name=snap["name"], action="verify", requested=dict(expected), **kwargs)

        return judge(build, expected, snap, snap["name"])
```

Add to `PluginRegistry` in `src/richard/plugins/registry.py`:

```python
    def plugin(self, name: str):
        """The plugin instance behind a record, if loaded (built-ins are registered in memory)."""
        record = self._records.get(name)
        return record.plugin if record is not None else None
```

Create `src/richard/plugins/perception/__init__.py`:

```python
"""Ambient perception as a built-in plugin: disabled means invisible to the model."""
from __future__ import annotations

from richard.plugins.base import PluginContext, PluginParts

CONTEXT = ("Ambient perception is on: you are told when someone arrives, leaves or is recognised, "
           "and you can look at the live camera with the camera tool.")


def _default_detector(settings, write):
    from richard.perception.detect import PersonDetector

    return PersonDetector.load(device=settings.device, threads=4, write=write, threshold=settings.person_threshold)


def _default_identifier(settings, gallery, write):
    if not settings.identity_enabled or gallery is None:
        return None
    from richard.perception.faces import FaceDetector, FaceEmbedder, FaceIdentifier

    return FaceIdentifier(FaceDetector.load(device=settings.device, write=write),
                          FaceEmbedder.load(device=settings.device, write=write), gallery)


class PerceptionPlugin:
    name = "perception"
    version = "0.1"

    def __init__(self, *, detector_factory=None, identifier_factory=None) -> None:
        self._detector_factory = detector_factory or _default_detector
        self._identifier_factory = identifier_factory or _default_identifier
        self.service = None

    def config_defaults(self) -> dict:
        return {"identity_enabled": False, "quiet_hours": "", "sensitivity": 50, "cooldown_s": 120,
                "enter_debounce_s": 2, "leave_debounce_s": 10, "stream_fps": 2, "keep_thumbnails": False,
                "device": "cpu", "stale_s": 5}

    def build(self, ctx: PluginContext) -> PluginParts:
        from richard.perception.camera import CameraProvider, PresenceProvider
        from richard.perception.pipeline import PerceptionService, Settings
        from richard.perception.reader import PerceptionTargetReader

        settings = Settings.from_table(ctx.config)
        detector = self._detector_factory(settings, ctx.write)
        service = PerceptionService(settings, data_dir=ctx.data_dir, person_detector=detector, write=ctx.write)
        service._identifier = self._identifier_factory(settings, service.gallery, ctx.write)
        service.start()
        self.service = service
        return PluginParts(
            providers=[CameraProvider(service), PresenceProvider(service)],
            target_readers={"perception": PerceptionTargetReader(service)},
            event_sources=[service.event_source],
            context=CONTEXT,
            shutdown=service.stop,
        )
```

- [ ] **Step 4: Run the tests to verify they pass, then commit**

Run: `.venv/bin/python -m pytest tests/test_plugin_perception.py tests/test_plugins_registry.py tests/test_cli_plugins.py -q`

```bash
git add src/richard/perception/reader.py src/richard/plugins/perception/__init__.py src/richard/plugins/registry.py tests/test_plugin_perception.py
git commit -m "perception: built-in plugin and the perception target kind

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 13: Session registry and `[perception]` context items

**Files:**
- Create: `src/richard/realtime/registry.py`
- Modify: `src/richard/realtime/session.py`, `src/richard/cli.py` (`_realtime_session_factory`)
- Test: `tests/test_realtime_registry.py`, `tests/test_realtime_session.py`

**Interfaces:**
- Produces: `SessionRegistry()` with `add(session)`, `remove(session)`, `active() -> list`, `offer_context(line: str) -> bool` (queues the line on every open session; `False` when none); `RealtimeSession(..., registry=None)` registers itself on construction and removes itself in `close()`; `RealtimeSession.add_context(line: str)`; before a turn runs, queued lines become ONE user-role message `"[perception] HH:MM line\n[perception] HH:MM line"` appended before the user's own text; `create_response()` treats queued context as something new. Spec one's server-initiated turns extend this registry; nothing here starts a turn by itself.
- The minimal registry is built here because the routing needs it now; spec one adds `prompt()`/`say()` on top.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_realtime_registry.py`:

```python
from richard.realtime.registry import SessionRegistry


class S:
    def __init__(self):
        self.lines = []

    def add_context(self, line):
        self.lines.append(line)


def test_registry_offers_context_to_open_sessions_only():
    reg = SessionRegistry()
    assert reg.offer_context("someone entered") is False
    a, b = S(), S()
    reg.add(a); reg.add(b)
    assert reg.active() == [a, b]
    assert reg.offer_context("matteo recognised") is True
    assert a.lines == ["matteo recognised"] and b.lines == ["matteo recognised"]
    reg.remove(a)
    reg.remove(a)  # idempotent
    assert reg.active() == [b]
```

Append to `tests/test_realtime_session.py`:

```python
from richard.realtime.registry import SessionRegistry  # noqa: E402


def test_session_registers_and_unregisters_itself():
    reg = SessionRegistry()
    session, emitted, done = collect_session(detector=ScriptedDetector([]), registry=reg)
    assert reg.active() == [session]
    session.close()
    assert reg.active() == []


def test_context_lines_become_one_perception_item_before_the_user_text():
    engine = FakeEngine()
    session, emitted, done = collect_session(engine=engine, detector=ScriptedDetector([]), clock_hm=lambda: "18:42")
    session.add_context("matteo recognised (browser)")
    session.add_context("the scene changed (browser)")
    session.create_item({"kind": "message", "content": "hi"})
    session.create_response()
    wait(done)
    seen = engine.seen[0]
    assert seen[0] == ("user", "[perception] 18:42 matteo recognised (browser)\n[perception] 18:42 the scene changed (browser)")
    assert seen[1] == ("user", "hi")
    session.close()


def test_queued_context_alone_makes_response_create_run_a_turn():
    engine = FakeEngine()
    session, emitted, done = collect_session(engine=engine, detector=ScriptedDetector([]), clock_hm=lambda: "18:42")
    session.create_item({"kind": "message", "content": "hi"})
    session.create_response()
    wait(done)
    done.clear()
    session.add_context("someone entered (browser)")
    session.create_response()
    wait(done)
    assert engine.seen[1][-1] == ("user", "[perception] 18:42 someone entered (browser)")
    assert "response.audio.delta" in [e["type"] for e in emitted]
    session.close()
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_realtime_registry.py tests/test_realtime_session.py -q`
Expected: FAIL, `ModuleNotFoundError` / `TypeError: unexpected keyword 'registry'`.

- [ ] **Step 3: Implement**

Create `src/richard/realtime/registry.py`:

```python
"""The open realtime sessions, so the rest of `richard serve` can reach a live
conversation. Perception offers context lines here; spec one adds server-initiated
turns (`prompt`, `say`) on the same registry."""
from __future__ import annotations

import threading


class SessionRegistry:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._sessions: list = []

    def add(self, session) -> None:
        with self._lock:
            if session not in self._sessions:
                self._sessions.append(session)

    def remove(self, session) -> None:
        with self._lock:
            if session in self._sessions:
                self._sessions.remove(session)

    def active(self) -> list:
        with self._lock:
            return list(self._sessions)

    def offer_context(self, line: str) -> bool:
        sessions = self.active()
        for session in sessions:
            session.add_context(line)
        return bool(sessions)
```

In `src/richard/realtime/session.py`: add `import time` and `import queue` is present; constructor gains `registry=None, clock_hm=lambda: time.strftime("%H:%M")`; after the existing fields:

```python
        self._registry = registry
        self._clock_hm = clock_hm
        self._context: list[str] = []
        self._context_lock = threading.Lock()
        if registry is not None:
            registry.add(self)
```

Add the method:

```python
    def add_context(self, line: str) -> None:
        """Queue a `[perception]` line for the next turn (any thread)."""
        with self._context_lock:
            self._context.append(f"[perception] {self._clock_hm()} {line}")

    def _drain_context(self) -> str | None:
        with self._context_lock:
            lines, self._context = self._context, []
        return "\n".join(lines) if lines else None
```

In `create_response()`, compute `nothing_new` as before but `and not self._context` (read under the lock via a small helper `self._has_context()`). In `_respond`, right after `seal_pending` and before `add_user(user_text)`:

```python
        context = self._drain_context()
        if context:
            self.conversation.add_user(context)
```

In `close()`, first line: `if self._registry is not None: self._registry.remove(self)`.

In `tests/test_realtime_session.py` `collect_session(**overrides)` already forwards keyword overrides to `RealtimeSession(**kw)`, so `registry=` and `clock_hm=` pass through.

In `src/richard/cli.py` `_realtime_session_factory(config, *, brain, providers_fn, synth, transcriber, vad_factory, registry=None)`: pass `registry=registry` to `RealtimeSession(...)`. The serve wiring for the registry lands in Task 16.

- [ ] **Step 4: Run the tests to verify they pass, then commit**

Run: `.venv/bin/python -m pytest tests/test_realtime_registry.py tests/test_realtime_session.py tests/test_cli.py -q`

```bash
git add src/richard/realtime/registry.py src/richard/realtime/session.py src/richard/cli.py tests/test_realtime_registry.py tests/test_realtime_session.py
git commit -m "realtime: session registry and [perception] context items before the next turn

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 14: Web API for perception

**Files:**
- Modify: `src/richard/web/app.py`
- Test: `tests/test_web.py`

**Interfaces:**
- Produces: `WebApp(..., perception: Callable[[], object | None] | None = None)` (a getter: the service exists only when the plugin is enabled). Routes: `GET /api/perception/status` → `{"enabled": false}` or `{"enabled": true, ...service.status()}`; `POST /api/perception/frame` body `{"source": "browser", "image_base64": "..."}` → `{"ok": true, "source": "browser"}` (400 on bad base64/undecodable, 413 over 8 MB, 503 when disabled); `GET /api/perception/events` and `GET /api/perception/events/<since_id>` → `{"events": [...]}`; `GET /api/perception/latest.jpg` → `image/jpeg` (404 when no live source); `GET /api/perception/gallery` → `{"people": [...]}`; `POST /api/perception/gallery` body `{"name": "...", "images_base64": [...]}` → `{"name", "samples"}` (400 on errors); `DELETE /api/perception/gallery/<name>`; `GET /api/perception/config` → the `[plugins.perception]` table with defaults applied; `PUT /api/perception/config` merges the given keys into the table, saves, returns `{"config": table, "restart_required": true}`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_web.py`:

```python
# --- perception ---

import base64 as _b64


class _PerceptionFake:
    def __init__(self):
        self.frames = []
        self.enrolled = []
        self.deleted = []
        self._live = []

        class Gallery:
            def list(inner):
                return [{"name": "matteo", "samples": 3, "enrolled_at": "2026-09-09"}]

            def delete(inner, name):
                self.deleted.append(name)
                return name == "matteo"
        self.gallery = Gallery()

    def push_frame(self, source, jpeg):
        if not jpeg.startswith(b"\xff\xd8"):
            raise ValueError("frame is not a decodable image")
        self.frames.append((source, jpeg))
        self._live = [source]

    def status(self):
        return {"sources": [{"id": "browser", "stale": False, "frames": len(self.frames)}], "presence": [], "gallery": self.gallery.list(), "identity_enabled": True}

    def recent_events(self, since_id=0, limit=100):
        return [{"id": 2, "kind": "person_entered"}] if since_id < 2 else []

    def snapshot(self, source_id=None, detail="low", region=None):
        return (b"\xff\xd8jpeg", {"source": "browser", "width": 800, "height": 450}) if self._live else None

    def enrol(self, name, jpegs):
        self.enrolled.append((name, len(jpegs)))
        return len(jpegs)


def _papp(tmp_path, service):
    config_path = tmp_path / "config.toml"
    cfg = Config()
    cfg.plugins.tables["perception"] = {"sensitivity": 70}
    save_config(cfg, config_path)
    return WebApp(config_path=config_path, memory_store=MemoryStore(":memory:"), relays=RelayRegistry(),
                  perception=lambda: service)


def test_perception_status_disabled_without_a_service(tmp_path):
    app = _app(tmp_path)
    assert _body(app.handle("GET", "/api/perception/status")) == {"enabled": False}
    assert app.handle("POST", "/api/perception/frame", b'{"source":"browser","image_base64":"AA=="}').status == 503


def test_perception_frame_events_and_latest(tmp_path):
    svc = _PerceptionFake()
    app = _papp(tmp_path, svc)
    assert app.handle("GET", "/api/perception/latest.jpg").status == 404
    body = json.dumps({"source": "browser", "image_base64": _b64.b64encode(b"\xff\xd8abc").decode()}).encode()
    resp = app.handle("POST", "/api/perception/frame", body)
    assert resp.status == 200 and _body(resp) == {"ok": True, "source": "browser"}
    assert svc.frames == [("browser", b"\xff\xd8abc")]
    bad = json.dumps({"source": "browser", "image_base64": _b64.b64encode(b"nope").decode()}).encode()
    assert app.handle("POST", "/api/perception/frame", bad).status == 400
    status = _body(app.handle("GET", "/api/perception/status"))
    assert status["enabled"] is True and status["sources"][0]["frames"] == 1
    assert _body(app.handle("GET", "/api/perception/events"))["events"] == [{"id": 2, "kind": "person_entered"}]
    assert _body(app.handle("GET", "/api/perception/events/2"))["events"] == []
    latest = app.handle("GET", "/api/perception/latest.jpg")
    assert latest.status == 200 and latest.content_type == "image/jpeg" and latest.body == b"\xff\xd8jpeg"


def test_perception_gallery_routes(tmp_path):
    svc = _PerceptionFake()
    app = _papp(tmp_path, svc)
    assert _body(app.handle("GET", "/api/perception/gallery"))["people"][0]["name"] == "matteo"
    shots = [_b64.b64encode(b"\xff\xd8a").decode()] * 3
    resp = app.handle("POST", "/api/perception/gallery", json.dumps({"name": "guest", "images_base64": shots}).encode())
    assert _body(resp) == {"name": "guest", "samples": 3} and svc.enrolled == [("guest", 3)]
    assert app.handle("POST", "/api/perception/gallery", b'{"name": "", "images_base64": []}').status == 400
    assert _body(app.handle("DELETE", "/api/perception/gallery/matteo")) == {"deleted": "matteo"}
    assert app.handle("DELETE", "/api/perception/gallery/ghost").status == 404


def test_perception_config_get_and_put(tmp_path):
    app = _papp(tmp_path, _PerceptionFake())
    cfg = _body(app.handle("GET", "/api/perception/config"))
    assert cfg["sensitivity"] == 70 and cfg["cooldown_s"] == 120 and cfg["identity_enabled"] is False
    resp = app.handle("PUT", "/api/perception/config", b'{"identity_enabled": true, "quiet_hours": "23:00-07:30", "bogus": 1}')
    out = _body(resp)
    assert out["restart_required"] is True and out["config"]["identity_enabled"] is True
    assert "bogus" not in out["config"]
    assert load_config(tmp_path / "config.toml").plugins.tables["perception"]["quiet_hours"] == "23:00-07:30"
    assert app.handle("PUT", "/api/perception/config", b'{"quiet_hours": "late"}').status == 400
```

`_body` is the existing helper in `tests/test_web.py` that JSON-decodes a `Response`.

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_web.py -q -k perception`
Expected: FAIL (`TypeError: unexpected keyword argument 'perception'`).

- [ ] **Step 3: Implement**

In `src/richard/web/app.py`: constructor gains `perception: Callable[[], object] | None = None` → `self._perception = perception`; a helper:

```python
    def _perception_service(self):
        return self._perception() if self._perception is not None else None
```

In `_dispatch`, before the 404 fallthrough:

```python
        if path.startswith("/api/perception/"):
            return self._perception_route(method, path, body)
```

Add the routes:

```python
    # --- perception ---

    _PERCEPTION_KEYS = ("identity_enabled", "quiet_hours", "sensitivity", "cooldown_s", "enter_debounce_s",
                        "leave_debounce_s", "stream_fps", "keep_thumbnails", "device", "stale_s")

    def _perception_route(self, method: str, path: str, body: bytes) -> Response:
        service = self._perception_service()
        if path == "/api/perception/status" and method == "GET":
            if service is None:
                return Response.json({"enabled": False})
            return Response.json({"enabled": True, **service.status()})
        if path == "/api/perception/config" and method in ("GET", "PUT"):
            return self._perception_config(method, body)
        if service is None:
            return Response.json({"error": "perception plugin is not enabled"}, 503)
        if path == "/api/perception/frame" and method == "POST":
            payload, err = self._parse_object(body)
            if err:
                return err
            source = str(payload.get("source") or "browser")[:32]
            try:
                jpeg = base64.b64decode(str(payload.get("image_base64", "")), validate=True)
            except (ValueError, binascii.Error):
                return Response.bad_request("image_base64 is not valid base64")
            if len(jpeg) > vision.IMAGE_MAX_BYTES:
                return Response.json({"error": "frame larger than 8 MB"}, 413)
            try:
                service.push_frame(source, jpeg)
            except ValueError as exc:
                return Response.bad_request(str(exc))
            return Response.json({"ok": True, "source": source})
        if path.startswith("/api/perception/events") and method == "GET":
            tail = path[len("/api/perception/events"):].strip("/")
            since = _as_int(tail, 0) if tail else 0
            return Response.json({"events": service.recent_events(since_id=since, limit=100)})
        if path == "/api/perception/latest.jpg" and method == "GET":
            shot = service.snapshot(detail="low")
            if shot is None:
                return Response.json({"error": "no live camera"}, 404)
            return Response(status=200, body=shot[0], content_type="image/jpeg")
        if path == "/api/perception/gallery" and method == "GET":
            return Response.json({"people": service.gallery.list() if service.gallery else []})
        if path == "/api/perception/gallery" and method == "POST":
            payload, err = self._parse_object(body)
            if err:
                return err
            name = str(payload.get("name") or "").strip()
            raw = payload.get("images_base64")
            if not name or not isinstance(raw, list) or not raw:
                return Response.bad_request("a name and at least one snapshot are required")
            try:
                jpegs = [base64.b64decode(str(item), validate=True) for item in raw]
                samples = service.enrol(name, jpegs)
            except (ValueError, binascii.Error) as exc:
                return Response.bad_request(str(exc))
            return Response.json({"name": name, "samples": samples})
        if path.startswith("/api/perception/gallery/") and method == "DELETE":
            name = path[len("/api/perception/gallery/"):]
            if service.gallery is None or not service.gallery.delete(name):
                return Response.json({"error": f"no person named {name}"}, 404)
            return Response.json({"deleted": name})
        return Response.not_found()

    def _perception_config(self, method: str, body: bytes) -> Response:
        from richard.plugins.perception import PerceptionPlugin

        config = self._load(self._config_path)
        table = {**PerceptionPlugin().config_defaults(), **config.plugins.table("perception")}
        if method == "GET":
            return Response.json(table)
        payload, err = self._parse_object(body)
        if err:
            return err
        from richard.perception.gate import parse_quiet_hours

        for key in self._PERCEPTION_KEYS:
            if key not in payload:
                continue
            value = payload[key]
            if key == "quiet_hours":
                try:
                    parse_quiet_hours(str(value or ""))
                except ValueError as exc:
                    return Response.bad_request(str(exc))
                table[key] = str(value or "")
            elif key in ("identity_enabled", "keep_thumbnails"):
                table[key] = _as_bool(value)
            elif key == "device":
                table[key] = "cuda" if str(value) == "cuda" else "cpu"
            elif key == "sensitivity":
                table[key] = min(100, max(0, _as_int(value, 50)))
            else:
                table[key] = _clamp_float(value, 0.0, 86400.0)
        config.plugins.tables["perception"] = table
        self._save(config, self._config_path)
        return Response.json({"config": table, "restart_required": True})
```

`_parse_object`, `_as_int`, `_as_bool`, `_clamp_float` already exist in `app.py`. The frame POST goes through the sync `handle()` (the body is one JPEG, ~60 KB): fine on the shared loop; `_serve_request` only special-cases `/api/chat`, `/api/voice`, `/api/home-assistant`.

- [ ] **Step 4: Run the tests to verify they pass, then commit**

Run: `.venv/bin/python -m pytest tests/test_web.py -q`

```bash
git add src/richard/web/app.py tests/test_web.py
git commit -m "web: perception API (status, frames, events, latest frame, gallery, config)

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 15: Web UI: Perception page and the home-page frame streamer

**Files:**
- Modify: `src/richard/web/static.py`
- Test: `tests/test_web.py`

**Interfaces (JS):**
- Produces: `perceptionStreamer` (start/stop on status + visibility), `loadPerception()`, `renderPerceptionEvents(rows)`, `enrolFace()`, header indicator `#perception-indicator`; Perception drawer page mounting windows `perception-content` and `perception-gallery-content`.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_web.py`:

```python
def test_home_page_has_the_perception_page_and_streamer(tmp_path):
    html = _app(tmp_path).handle("GET", "/").body.decode()
    assert 'data-drawer-open="perception"' in html and 'data-drawer-page="perception"' in html
    assert 'id="perception-content"' in html and 'id="perception-gallery-content"' in html
    for field in ("identity_enabled", "quiet_hours", "sensitivity", "cooldown_s", "stream_fps", "keep_thumbnails"):
        assert f'id="perception.{field}"' in html
    assert 'id="perception-events"' in html and 'id="perception-latest"' in html
    assert 'id="perception-indicator"' in html
    assert "function startPerceptionStream(" in html and "'/api/perception/frame'" in html
    assert "function enrolFace(" in html and "'/api/perception/gallery'" in html
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_web.py -q -k perception_page`

- [ ] **Step 3: Implement**

All edits inside `SPA_HTML`.

(a) Menu: in the `configuration` page's `<nav>` add after the Plugins button:

```html
        <button type="button" data-drawer-open="perception">Perception <span>CAMERA ›</span></button>
```

and change `<span>05 PAGES</span>` to `<span>06 PAGES</span>`.

(b) Page: after the `plugins` drawer page section add:

```html
    <section class="drawer-page" data-drawer-page="perception" hidden>
      <button type="button" class="drawer-back" data-drawer-back="configuration">‹ Back to configuration</button>
      <div class="drawer-title" tabindex="-1"><b>&gt; PERCEPTION</b><span>AMBIENT CAMERA</span></div>
      <div class="drawer-page-body" data-panel-content="perception-content perception-gallery-content"></div>
    </section>
```

(c) Header indicator: inside the header's right block (next to `id="connection-status"`) add `<span id="perception-indicator" class="status-indicator" hidden>◉ camera</span>`.

(d) Windows: after the `system.home_assistant.api` terminal section add:

```html
    <!-- system.perception.ambient -->
    <div class="terminal-section">
      <div class="terminal-header-line" data-target="perception-content">
        <span class="terminal-prompt">&gt; </span>
        <span class="terminal-command window-title">system.perception.ambient</span>
        <span class="window-control is-open">[_]</span>
      </div>
      <div class="window-content" id="perception-content">
        <p class="lede">Richard watches the camera continuously at almost no cost and is told only about transitions: someone arrived, left, was recognised, the scene changed. Enable the plugin, allow the camera on this device, and restart to apply settings.</p>
        <div class="setting-group"><div class="checkbox-btn"><input type="checkbox" id="perception.plugin_enabled"><label for="perception.plugin_enabled">Perception plugin enabled</label><span class="checkmark"></span></div><span class="hint">Disabled means invisible to Richard: no events, no camera tool, no stream accepted.</span></div>
        <div class="setting-group"><div class="checkbox-btn"><input type="checkbox" id="perception.identity_enabled"><label for="perception.identity_enabled">Recognise enrolled people</label><span class="checkmark"></span></div><span class="hint">Opt-in. Faces are matched against the local gallery below; nothing leaves this box.</span></div>
        <div class="field-row">
          <div class="setting-group"><label class="setting-label">Quiet hours</label><input id="perception.quiet_hours" class="setting-input" placeholder="23:00-07:30"><span class="hint">No events reach Richard in this window.</span></div>
          <div class="setting-group"><label class="setting-label">Sensitivity</label><input id="perception.sensitivity" class="setting-input" type="number" min="0" max="100"></div>
          <div class="setting-group"><label class="setting-label">Cooldown (s)</label><input id="perception.cooldown_s" class="setting-input" type="number" min="0"></div>
        </div>
        <div class="field-row">
          <div class="setting-group"><label class="setting-label">Stream frames per second</label><input id="perception.stream_fps" class="setting-input" type="number" min="0.5" max="10" step="0.5"></div>
          <div class="setting-group"><div class="checkbox-btn"><input type="checkbox" id="perception.keep_thumbnails"><label for="perception.keep_thumbnails">Keep a thumbnail per event</label><span class="checkmark"></span></div></div>
        </div>
        <div class="button-row"><button class="setting-button" id="perception-save">Save changes</button><button class="setting-button small" id="perception-restart">Restart to apply</button><div class="status-line" id="perception-status"></div></div>
        <label class="setting-label spread">Live <span id="perception-sources">—</span></label>
        <img id="perception-latest" class="chat-thumb" alt="latest frame" hidden>
        <label class="setting-label spread">Events <span id="perception-presence">nobody in view</span></label>
        <div class="entry-list" id="perception-events" style="max-height:260px;overflow-y:auto;"><div class="empty-message">No events yet.</div></div>
      </div>
    </div>

    <!-- system.perception.gallery -->
    <div class="terminal-section">
      <div class="terminal-header-line" data-target="perception-gallery-content">
        <span class="terminal-prompt">&gt; </span>
        <span class="terminal-command window-title">system.perception.gallery</span>
        <span class="window-control is-open">[_]</span>
      </div>
      <div class="window-content" id="perception-gallery-content">
        <p class="lede">People Richard may recognise. Enrolment takes three snapshots from this device's camera; each entry can be deleted at any time and is then forgotten for good.</p>
        <div class="field-row">
          <div class="setting-group"><label class="setting-label">Name</label><input id="perception-enrol-name" class="setting-input" placeholder="Matteo"></div>
          <div class="setting-group"><label class="setting-label">&nbsp;</label><button class="setting-button" id="perception-enrol">Enrol from camera</button></div>
        </div>
        <div class="status-line" id="perception-gallery-status"></div>
        <div class="entry-list" id="perception-gallery"><div class="empty-message">Nobody enrolled.</div></div>
      </div>
    </div>
```

(e) JS. Add before `let activeDrawerPage = 'menu';`:

```js
/* ---- ambient perception: this device's camera as a frame source, and the page ---- */
const perception = {video: null, stream: null, timer: null, enabled: false, fps: 2, lastEventId: 0, poll: null};
async function startPerceptionStream(){
  if (perception.timer || !navigator.mediaDevices || !navigator.mediaDevices.getUserMedia) return;
  try {
    perception.stream = await navigator.mediaDevices.getUserMedia({video: {width: {ideal: 1280}, height: {ideal: 720}, facingMode: 'user'}});
  } catch (e) { setStatus('perception', 'camera blocked: ' + e.message, 'err'); return; }
  const v = document.createElement('video');
  v.muted = true; v.playsInline = true; v.autoplay = true; v.hidden = true;
  v.srcObject = perception.stream; document.body.appendChild(v);
  try { await v.play(); } catch (_) {}
  perception.video = v;
  $('perception-indicator').hidden = false;
  perception.timer = setInterval(async () => {
    if (!perception.video || !perception.video.videoWidth) return;
    const shot = frameFromCanvasSource(perception.video, perception.video.videoWidth, perception.video.videoHeight, 800, 0.7);
    if (!shot) return;
    try { await sendJSON('/api/perception/frame', 'POST', {source: 'browser', image_base64: shot.url.split(',')[1]}); }
    catch (e) { /* the next frame retries; the status line reports it */ setStatus('perception', 'stream: ' + e.message, 'err'); }
  }, Math.max(100, 1000 / perception.fps));
}
function stopPerceptionStream(){
  if (perception.timer) { clearInterval(perception.timer); perception.timer = null; }
  if (perception.stream) { perception.stream.getTracks().forEach(t => t.stop()); perception.stream = null; }
  if (perception.video) { perception.video.remove(); perception.video = null; }
  $('perception-indicator').hidden = true;
}
function capturePerceptionFrame(maxEdge){
  const v = perception.video || (rt && rt.video);
  return v && v.videoWidth ? frameFromCanvasSource(v, v.videoWidth, v.videoHeight, maxEdge, 0.85) : null;
}
async function syncPerceptionStream(){
  let status;
  try { status = await getJSON('/api/perception/status'); } catch (_) { return; }
  perception.enabled = !!status.enabled;
  if (perception.enabled && document.visibilityState === 'visible') startPerceptionStream();
  else stopPerceptionStream();
  return status;
}
document.addEventListener('visibilitychange', () => { syncPerceptionStream(); });

function renderPerceptionEvents(rows){
  const box = $('perception-events');
  if (!rows.length) { box.innerHTML = '<div class="empty-message">No events yet.</div>'; return; }
  box.innerHTML = rows.slice().reverse().map(r =>
    '<div class="entry"><div class="entry-main"><div class="entry-name">' + esc(r.kind.replace(/_/g, ' ')) + (r.subject ? ' · ' + esc(r.subject) : '') + '</div>' +
    '<div class="entry-sub">' + esc((r.at || '').replace('T', ' ').slice(0, 19)) + ' · ' + esc(r.source) + '</div></div></div>').join('');
}
function renderGallery(people){
  const box = $('perception-gallery');
  if (!people.length) { box.innerHTML = '<div class="empty-message">Nobody enrolled.</div>'; return; }
  box.innerHTML = people.map(p => '<div class="entry"><div class="entry-main"><div class="entry-name">' + esc(p.name) + '</div><div class="entry-sub">' + p.samples + ' snapshots · ' + esc(p.enrolled_at || '') + '</div></div>' +
    '<button class="setting-button small" data-gallery-delete="' + esc(p.name) + '">Delete</button></div>').join('');
}
async function loadPerception(){
  try {
    const cfg = await getJSON('/api/perception/config');
    for (const key of ['quiet_hours', 'sensitivity', 'cooldown_s', 'stream_fps']) $('perception.' + key).value = cfg[key];
    $('perception.identity_enabled').checked = !!cfg.identity_enabled;
    $('perception.keep_thumbnails').checked = !!cfg.keep_thumbnails;
    const plugins = (await getJSON('/api/plugins')).plugins;
    const row = plugins.find(p => p.name === 'perception');
    $('perception.plugin_enabled').checked = !!(row && row.configured);
    const status = await syncPerceptionStream();
    if (status && status.enabled) {
      $('perception-sources').textContent = status.sources.map(s => s.id + (s.stale ? ' (stale)' : '') + ' · ' + s.frames + ' frames').join(', ') || 'no source yet';
      $('perception-presence').textContent = status.presence.length ? status.presence.map(p => p.subject + ' since ' + Math.round(p.since)).join(', ') : 'nobody in view';
      renderGallery(status.gallery || []);
      const ev = await getJSON('/api/perception/events');
      renderPerceptionEvents(ev.events);
      const img = $('perception-latest'); img.src = '/api/perception/latest.jpg?t=' + Date.now(); img.hidden = false;
    } else {
      $('perception-sources').textContent = 'plugin disabled';
      $('perception-latest').hidden = true;
    }
  } catch (e) { setStatus('perception', 'unavailable: ' + e.message, 'err'); }
}
async function savePerception(){
  const patch = {
    quiet_hours: $('perception.quiet_hours').value.trim(), sensitivity: Number($('perception.sensitivity').value),
    cooldown_s: Number($('perception.cooldown_s').value), stream_fps: Number($('perception.stream_fps').value),
    identity_enabled: $('perception.identity_enabled').checked, keep_thumbnails: $('perception.keep_thumbnails').checked,
  };
  try {
    await sendJSON('/api/perception/config', 'PUT', patch);
    const wantPlugin = $('perception.plugin_enabled').checked;
    await sendJSON('/api/plugins', 'PUT', {name: 'perception', enabled: wantPlugin});
    setStatus('perception', 'saved · restart to apply · ' + hm(), 'ok');
  } catch (e) { setStatus('perception', 'save failed: ' + e.message, 'err'); }
}
async function enrolFace(){
  const name = $('perception-enrol-name').value.trim();
  if (!name) { setStatus('perception-gallery', 'a name is required', 'err'); return; }
  if (!perception.video && !(rt && rt.video)) { await startPerceptionStream(); await new Promise(r => setTimeout(r, 800)); }
  const shots = [];
  for (let i = 0; i < 3; i++) {
    const shot = capturePerceptionFrame(800);
    if (shot) shots.push(shot.url.split(',')[1]);
    await new Promise(r => setTimeout(r, 700));
  }
  if (shots.length < 3) { setStatus('perception-gallery', 'camera not ready', 'err'); return; }
  try {
    const out = await sendJSON('/api/perception/gallery', 'POST', {name, images_base64: shots});
    setStatus('perception-gallery', out.name + ' enrolled (' + out.samples + ' snapshots) · ' + hm(), 'ok');
    renderGallery((await getJSON('/api/perception/gallery')).people);
  } catch (e) { setStatus('perception-gallery', 'enrol failed: ' + e.message, 'err'); }
}
$('perception-save').addEventListener('click', savePerception);
$('perception-restart').addEventListener('click', () => sendJSON('/api/restart', 'POST').catch(() => {}));
$('perception-enrol').addEventListener('click', enrolFace);
$('perception-gallery').addEventListener('click', async e => {
  const b = e.target.closest('[data-gallery-delete]'); if (!b) return;
  try { await fetch('/api/perception/gallery/' + encodeURIComponent(b.dataset.galleryDelete), {method: 'DELETE'}); renderGallery((await getJSON('/api/perception/gallery')).people); }
  catch (err) { setStatus('perception-gallery', 'delete failed: ' + err.message, 'err'); }
});
document.querySelector('[data-drawer-open="perception"]').addEventListener('click', () => {
  loadPerception();
  if (perception.poll) clearInterval(perception.poll);
  perception.poll = setInterval(() => { if (activeDrawerPage === 'perception') loadPerception(); else { clearInterval(perception.poll); perception.poll = null; } }, 3000);
});
```

Note: `frameFromCanvasSource(source, srcW, srcH, maxEdge, quality)` exists since spec 3a; `hm()`, `sendJSON`, `getJSON`, `setStatus`, `esc` exist. `/api/restart` exists (POST).

(f) Start the streamer with the page: in `refreshAll()` (the function that loads everything at start) add `try { await syncPerceptionStream(); } catch (e) {}` after the plugins load, and add a `setInterval(() => { syncPerceptionStream().catch(() => {}); }, 30000);` next to the existing status interval at the end of the script. The first `getUserMedia` asks once; a refusal shows in the Perception status line and the page keeps working.

- [ ] **Step 4: Run the tests and check the page**

Run: `.venv/bin/python -m pytest tests/test_web.py -q`

Dump and view (Chrome refuses `file://`):

```bash
.venv/bin/python -c "from richard.web.static import SPA_HTML; open('/tmp/richard-spa.html','w').write(SPA_HTML)"
(cd /tmp && python3 -m http.server 8765 --bind 127.0.0.1 >/dev/null 2>&1 &)
```

Open `http://127.0.0.1:8765/richard-spa.html?v=1`, open the menu → Configuration → Perception; the two windows render with the fields, the events box and the gallery; no console errors from the page itself (the extension's "message channel closed" lines are not the page's).

- [ ] **Step 5: Commit**

```bash
git add src/richard/web/static.py tests/test_web.py
git commit -m "web ui: Perception page (settings, live status, events, gallery) and the camera frame streamer

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 16: Serve wiring, docs, full suite

**Files:**
- Modify: `src/richard/cli.py`, `README.md`
- Create: `docs/perception.md`
- Test: `tests/test_cli.py`

**Interfaces:**
- Consumes: `PluginRegistry.plugin("perception")`, `PerceptionPlugin.service`, `SessionRegistry`, `WebApp(perception=...)`, `_realtime_session_factory(..., registry=)`.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_cli.py`:

```python
def test_serve_wiring_helper_finds_the_perception_service():
    from richard.cli import _perception_service_of
    from richard.plugins.perception import PerceptionPlugin
    from richard.plugins.registry import PluginRegistry

    plugin = PerceptionPlugin(detector_factory=lambda s, w: None, identifier_factory=lambda s, g, w: None)
    registry = PluginRegistry([plugin])
    assert _perception_service_of(registry) is None  # not built
    registry.build(["perception"], {}, persona_name="R", data_dir=__import__("tempfile").mkdtemp(), write=lambda s: None)
    assert _perception_service_of(registry) is plugin.service
    registry.shutdown()
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_cli.py -q -k perception_service`

- [ ] **Step 3: Implement**

In `src/richard/cli.py` add:

```python
def _perception_service_of(registry):
    """The running PerceptionService when the perception plugin is enabled and built."""
    plugin = registry.plugin("perception")
    return getattr(plugin, "service", None) if plugin is not None else None
```

In `_run_serve`, after `registry = _build_plugins(config, write)`:

```python
    from richard.realtime.registry import SessionRegistry

    session_registry = SessionRegistry()
    perception_service = _perception_service_of(registry)
    if perception_service is not None:
        # Admitted events go into an open realtime conversation as [perception] context;
        # with no session open they fall through to the control-loop monitor and the log.
        perception_service.set_context_sink(session_registry.offer_context)
```

Pass `perception=lambda: _perception_service_of(registry)` to `WebApp(...)` and `registry=session_registry` to `_realtime_session_factory(...)`.

Create `docs/perception.md`:

```markdown
# Ambient perception

Richard watches continuously and speaks rarely. Three stages:

1. **Sensor.** Frame differencing (numpy) for activity and scene changes; SSD-MobileNetV1
   (ONNX model zoo, MIT) for people; UltraFace (MIT) + ArcFace int8 (Apache-2.0) for
   opt-in recognition against a local gallery. Models download to `~/.richard/models` on
   first use (about 95 MB in total).
2. **Gate.** Debounce (2 s to enter, 10 s to leave, two agreeing matches to be named),
   transitions only, a cooldown per event and person, quiet hours, a master switch.
3. **Brain.** An admitted event becomes a `[perception] HH:MM ...` line in the open realtime
   conversation before its next turn, or a control-loop event (`perception:person_present`,
   `perception:identified:<name>`) when nobody is talking. Richard can look himself with
   `camera(question, detail, region)`: low = 800 px, high = the source's native frame,
   region = a crop at native resolution (`left|right|centre|top|bottom` or `x0,y0,x1,y1`).
   `who_is_here` and `last_seen(name)` answer from the presence log.

Enable: `richard plugins enable perception`, install the extra
(`pip install "richard-companion[perception]"`), restart, open the web UI's Perception page
and allow the camera. The browser streams 800 px JPEG frames at 2 fps while the tab is
visible; the header shows "◉ camera" while it does. Everything stays on the box; the
gallery is enrolled from the page and deletable there.

Config (`[plugins.perception]`): `identity_enabled`, `quiet_hours`, `sensitivity` (0-100),
`cooldown_s`, `enter_debounce_s`, `leave_debounce_s`, `stream_fps`, `keep_thumbnails`,
`device` (`cpu`|`cuda`), `stale_s`.

Logs: `perception: <event>` and `perception: dropped <kind> (<reason>)` on the `richard.perception`
logger. Robot source and wake-ups when nobody is talking: spec four and spec one.
```

README: after the **Vision** bullet add:

```markdown
- **Ambient perception** — with the `perception` plugin Richard watches the camera
  continuously, notices who arrives and leaves (recognition is opt-in and local), and is told
  only about transitions. See [`docs/perception.md`](docs/perception.md).
```

- [ ] **Step 4: Run the full suite, then commit**

Run: `.venv/bin/python -m pytest -q`
Expected: everything green, count ≥ 667 + the new tests.

```bash
git add src/richard/cli.py docs/perception.md README.md tests/test_cli.py
git commit -m "serve: wire perception into the web app and realtime sessions; docs

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

## Deploy and live verification (after the plan, on Matteo's go)

Restarting `richard.service` drops his live session: ask first.

```bash
~/Documents/GitHub/inference-box/bin/rbox 'cd /opt/richard && git pull --ff-only && /root/.local/bin/uv pip install --python .venv/bin/python -e ".[perception]" && .venv/bin/richard plugins enable perception && systemctl restart richard && sleep 8 && journalctl -u richard --since "-1min" --no-pager | tail -15'
```

The first start downloads ~28 MB (person detector) and, with identity on, another ~67 MB; watch for `Downloading ... for perception`. Then: (1) web UI → Configuration → Perception: allow the camera, "◉ camera" appears in the header, the Live line shows `browser · N frames`; (2) walk out of view for 10 s and back: `person left` / `someone entered` in the events box and `perception:` lines in the journal; (3) enrol yourself (3 snapshots), enable recognition, restart: `matteo recognised` follows the next entry; (4) in voice mode, ask "who is here?" and "look at the left side of the room"; (5) one dated line in `/root/CHANGELOG.md`, `bin/pull-box-docs`, path-scoped commit in `inference-box`. The robot source (SDK WebRTC on the box, dual-consumer check) is spec four's body plugin.
