from datetime import datetime, timedelta, timezone

import pytest

from richard.schedules import Schedule, describe_schedule, next_run, parse_schedule

_NOW = datetime(2026, 7, 16, 12, 0, 0, tzinfo=timezone.utc)  # a Thursday


def test_parse_at_with_days_roundtrips():
    schedule = parse_schedule({"at": "08:00", "days": ["Mon", "fri"]})
    assert schedule.kind == "at"
    assert schedule.days == ("mon", "fri")
    assert schedule.to_payload() == {"at": "08:00", "days": ["mon", "fri"]}


def test_parse_every_and_once_roundtrip():
    assert parse_schedule({"every_seconds": 3600}).to_payload() == {"every_seconds": 3600.0}
    assert parse_schedule({"once_at": "2026-07-16T18:30:00"}).to_payload() == {
        "once_at": "2026-07-16T18:30:00"
    }


@pytest.mark.parametrize(
    "payload,match",
    [
        ({}, "exactly one"),
        ({"at": "08:00", "every_seconds": 60}, "exactly one"),
        ({"at": "8am"}, "HH:MM"),
        ({"at": "25:00"}, "valid 24h"),
        ({"every_seconds": 3600, "days": ["mon"]}, "only valid together with at"),
        ({"at": "08:00", "days": ["monday"]}, "unknown weekday"),
        ({"every_seconds": 5}, "between 60 and 604800"),
        ({"every_seconds": "soon"}, "must be a number"),
        ({"once_at": "tomorrow"}, "ISO datetime"),
        ({"at": "08:00", "cron": "* *"}, "unknown schedule fields"),
        ("daily", "must be an object"),
    ],
)
def test_parse_rejects_invalid_shapes(payload, match):
    with pytest.raises(ValueError, match=match):
        parse_schedule(payload)


def test_describe_schedule_is_human_readable():
    assert describe_schedule(parse_schedule({"at": "08:00"})) == "daily at 08:00"
    assert describe_schedule(parse_schedule({"at": "08:00", "days": ["mon", "fri"]})) == "mon/fri at 08:00"
    assert describe_schedule(parse_schedule({"every_seconds": 1800})) == "every 30m"
    assert describe_schedule(parse_schedule({"every_seconds": 7200})) == "every 2h"
    assert describe_schedule(parse_schedule({"once_at": "2026-07-16T18:30:00"})) == (
        "once at 2026-07-16T18:30:00"
    )


def test_next_run_at_later_today_or_tomorrow():
    schedule = parse_schedule({"at": "14:30"})
    assert next_run(schedule, _NOW) == _NOW.replace(hour=14, minute=30)
    after = _NOW.replace(hour=15)
    assert next_run(schedule, after) == (_NOW + timedelta(days=1)).replace(hour=14, minute=30)


def test_next_run_at_boundary_is_strictly_after_now():
    schedule = parse_schedule({"at": "12:00"})
    assert next_run(schedule, _NOW) == (_NOW + timedelta(days=1)).replace(hour=12, minute=0)


def test_next_run_honors_weekday_filter():
    # _NOW is Thursday; next mon after Thursday 12:00 is in 4 days.
    schedule = parse_schedule({"at": "08:00", "days": ["mon"]})
    expected = (_NOW + timedelta(days=4)).replace(hour=8, minute=0, second=0, microsecond=0)
    assert next_run(schedule, _NOW) == expected


def test_next_run_every_anchors_to_now():
    schedule = parse_schedule({"every_seconds": 600})
    assert next_run(schedule, _NOW) == _NOW + timedelta(seconds=600)


def test_next_run_once_returns_the_moment_even_when_past():
    schedule = parse_schedule({"once_at": "2026-07-16T09:00:00+00:00"})
    assert next_run(schedule, _NOW) == datetime(2026, 7, 16, 9, 0, tzinfo=timezone.utc)


def test_next_run_requires_aware_now():
    with pytest.raises(ValueError, match="timezone-aware"):
        next_run(parse_schedule({"every_seconds": 600}), datetime(2026, 7, 16, 12, 0))
