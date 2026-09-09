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
