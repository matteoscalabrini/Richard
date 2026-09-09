import base64
import logging

import pytest

from richard.conversation import Message, user_parts
from richard.vision import (
    IMAGE_MAX_BYTES,
    check_image_data_url,
    count_images,
    image_urls,
    log_client_call,
    log_history,
    log_image,
)

JPEG = "data:image/jpeg;base64," + base64.b64encode(b"\xff\xd8\xff\xe0 fake jpeg").decode()


def test_check_accepts_jpeg_png_webp_and_reports_mime_and_size():
    assert check_image_data_url(JPEG) == ("image/jpeg", 14)
    png = "data:image/png;base64," + base64.b64encode(b"\x89PNG").decode()
    assert check_image_data_url(png) == ("image/png", 4)
    webp = "data:image/webp;base64," + base64.b64encode(b"RIFF").decode()
    assert check_image_data_url(webp) == ("image/webp", 4)


@pytest.mark.parametrize("url,fragment", [
    ("http://example.com/a.jpg", "data:image"),
    ("data:image/gif;base64,R0lG", "data:image"),
    ("data:image/jpeg;base64,@@@", "base64"),
    ("data:image/jpeg;base64,", "empty"),
    (None, "data URL"),
    (12, "data URL"),
])
def test_check_rejects_bad_urls(url, fragment):
    with pytest.raises(ValueError, match=fragment):
        check_image_data_url(url)


def test_check_rejects_oversized_payload():
    big = "data:image/jpeg;base64," + base64.b64encode(b"x" * (IMAGE_MAX_BYTES + 1)).decode()
    with pytest.raises(ValueError, match="8 MB"):
        check_image_data_url(big)


def test_image_urls_and_count_images():
    assert image_urls("plain") == []
    assert image_urls(user_parts("t", [JPEG, JPEG])) == [JPEG, JPEG]
    history = [Message("user", "hi"), Message("user", user_parts(None, [JPEG])), Message("assistant", "ok"),
               Message("user", user_parts("x", [JPEG]))]
    assert count_images(history) == 2


def test_log_lines_are_greppable(caplog):
    with caplog.at_level(logging.INFO, logger="richard.vision"):
        log_image("client", "image/jpeg", 1234, 2)
        log_history("web", 3)
        log_client_call("camera", '{"question": "what"}')
    assert "vision: image source=client mime=image/jpeg bytes=1234 history_images=2" in caplog.text
    assert "vision: history source=web images=3" in caplog.text
    assert 'vision: client tool call name=camera arguments={"question": "what"}' in caplog.text
