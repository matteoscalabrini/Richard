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
