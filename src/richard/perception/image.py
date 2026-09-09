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


def resize_exact(rgb: np.ndarray, width: int, height: int) -> np.ndarray:
    from PIL import Image

    im = Image.fromarray(np.ascontiguousarray(rgb, dtype=np.uint8), "RGB").resize((width, height), Image.BILINEAR)
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
