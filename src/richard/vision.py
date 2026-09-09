"""Vision helpers shared by the web and realtime edges.

The core has no image decoder (numpy only): an image is validated by its data-URL
header and decoded size, then passed to the brain untouched. Sizing happens where
the pixels are (the browser resizes to an 800 px long edge; the robot sends its native
frame). Telemetry is a few INFO lines on the `richard.vision` logger so p50/p95 can be
computed from logs later, as for the spoken path.
"""
from __future__ import annotations

import base64
import binascii
import logging
import re

from richard.conversation import Message

log = logging.getLogger("richard.vision")

IMAGE_MAX_BYTES = 8 * 1024 * 1024
_DATA_URL = re.compile(r"^data:image/(jpeg|png|webp);base64,(.*)$", re.DOTALL)


def check_image_data_url(url) -> tuple[str, int]:
    """Validate an image data URL. Returns (mime, decoded size). Raises ValueError."""
    if not isinstance(url, str):
        raise ValueError("image must be a data URL string")
    match = _DATA_URL.match(url)
    if not match:
        raise ValueError("image must be a data:image/(jpeg|png|webp);base64 URL")
    try:
        raw = base64.b64decode(match.group(2), validate=True)
    except (binascii.Error, ValueError) as exc:
        raise ValueError("image payload is not valid base64") from exc
    if not raw:
        raise ValueError("image payload is empty")
    if len(raw) > IMAGE_MAX_BYTES:
        raise ValueError(f"image is larger than {IMAGE_MAX_BYTES // (1024 * 1024)} MB")
    return f"image/{match.group(1)}", len(raw)


def image_urls(content) -> list[str]:
    """The image data URLs in a message's content (none for string content)."""
    if not isinstance(content, list):
        return []
    urls = []
    for part in content:
        if isinstance(part, dict) and part.get("type") == "image_url":
            ref = part.get("image_url")
            if isinstance(ref, dict) and isinstance(ref.get("url"), str):
                urls.append(ref["url"])
    return urls


def count_images(messages: list[Message]) -> int:
    return sum(len(image_urls(m.content)) for m in messages)


def log_image(source: str, mime: str, nbytes: int, history_images: int) -> None:
    log.info("vision: image source=%s mime=%s bytes=%d history_images=%d",
             source, mime, nbytes, history_images)


def log_history(source: str, images: int) -> None:
    """The HTTP paths re-send the whole history each request and cannot tell a new image
    from an old one; they log the count instead."""
    log.info("vision: history source=%s images=%d", source, images)


def log_client_call(name: str, arguments: str) -> None:
    log.info("vision: client tool call name=%s arguments=%s", name, arguments)
