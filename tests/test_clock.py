from datetime import datetime, timezone

from richard.clock import local_now, now_line, stamp, strip_now_line


def test_now_line_format():
    now = datetime(2026, 9, 16, 19, 31, tzinfo=timezone.utc)
    assert now_line(now) == "Now: Wednesday 2026-09-16 19:31 UTC"


def test_strip_now_line_drops_a_leading_now_line():
    assert strip_now_line("Now: Wednesday 2026-09-16 19:31 UTC\nhello") == "hello"


def test_strip_now_line_leaves_text_without_a_now_line_untouched():
    assert strip_now_line("hello") == "hello"
    assert strip_now_line("Nowhere to be found") == "Nowhere to be found"


def test_strip_now_line_handles_empty_and_none():
    assert strip_now_line("") == ""
    assert strip_now_line(None) == ""


def test_strip_now_line_with_only_the_now_line_returns_empty():
    assert strip_now_line("Now: Wednesday 2026-09-16 19:31 UTC") == ""


def test_stamp_converts_utc_iso_to_zone():
    assert stamp("2026-09-16T17:31:00+00:00", "Europe/Rome") == "2026-09-16 19:31"
    assert stamp("garbage", "Europe/Rome") == ""


def test_local_now_uses_zone_when_valid():
    assert local_now("Europe/Rome").tzinfo is not None
    assert local_now("Not/AZone").tzinfo is not None  # falls back to system local
