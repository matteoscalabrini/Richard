from datetime import datetime, timezone

from richard.clock import local_now, now_line, stamp


def test_now_line_format():
    now = datetime(2026, 9, 16, 19, 31, tzinfo=timezone.utc)
    assert now_line(now) == "Now: Wednesday 2026-09-16 19:31 UTC"


def test_stamp_converts_utc_iso_to_zone():
    assert stamp("2026-09-16T17:31:00+00:00", "Europe/Rome") == "2026-09-16 19:31"
    assert stamp("garbage", "Europe/Rome") == ""


def test_local_now_uses_zone_when_valid():
    assert local_now("Europe/Rome").tzinfo is not None
    assert local_now("Not/AZone").tzinfo is not None  # falls back to system local
