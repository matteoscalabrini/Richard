from __future__ import annotations

import json
import stat
from datetime import datetime, timezone
from pathlib import Path

import pytest

from richard.catchup import SessionState, build_digest, default_session_state_path


# --- build_digest ---------------------------------------------------------

def test_default_session_state_path():
    assert default_session_state_path() == Path.home() / ".richard" / "session_state.json"


def test_no_digest_on_first_run():
    """last_ended_at is None (no prior state file) -> no digest at all."""
    assert build_digest(
        now=datetime(2026, 9, 17, 6, 58, tzinfo=timezone.utc),
        tz_name="Europe/Rome",
        last_ended_at=None,
        perception_events=[{"at": "2026-09-16T20:14:00+00:00", "kind": "person_left",
                             "subject": "Matteo", "source": "browser"}],
        notifications=[],
        lights_on=["Lampada"],
        present=[],
    ) is None


def test_no_digest_when_nothing_happened():
    assert build_digest(
        now=datetime(2026, 9, 17, 6, 58, tzinfo=timezone.utc),
        tz_name="Europe/Rome",
        last_ended_at="2026-09-16T19:58:00+00:00",
        perception_events=[],
        notifications=[],
        lights_on=None,
        present=[],
    ) is None


def test_exact_example_from_brief():
    events = [
        {"at": "2026-09-16T20:14:00+00:00", "kind": "person_left", "subject": "Matteo", "source": "browser"},
        {"at": "2026-09-17T05:40:00+00:00", "kind": "person_entered", "subject": "", "source": "browser"},
    ] + [
        {"at": "2026-09-17T02:00:00+00:00", "kind": "scene_changed", "subject": "", "source": "browser"}
        for _ in range(6)
    ] + [
        {"at": "2026-09-17T03:00:00+00:00", "kind": "stillness", "subject": "5", "source": "browser"}
        for _ in range(2)
    ]
    notifications = [
        {"loop_name": "kitchen light", "summary": "the light was left on overnight",
         "created_at": "2026-09-17T05:55:00+00:00"},
    ]
    digest = build_digest(
        now=datetime(2026, 9, 17, 6, 58, tzinfo=timezone.utc),
        tz_name="Europe/Rome",
        last_ended_at="2026-09-16T19:58:00+00:00",
        perception_events=events,
        notifications=notifications,
        lights_on=["Lampada Scrivania", "Lampade Salotto"],
        present=[{"subject": "Matteo", "source": "browser"}],
    )
    expected = (
        "[catch-up] Background since the last conversation ended 2026-09-16 21:58 (11 h ago). "
        "Use it only to answer questions or ground what you say; do not report it unless asked.\n"
        "- 22:14 Matteo is no longer in the camera frame (browser)\n"
        "- 07:40 someone appeared in the camera frame (browser)\n"
        "- camera view changed 6 times; 2 still periods\n"
        '- loop "kitchen light" 07:55: the light was left on overnight\n'
        "- lights on now: Lampada Scrivania, Lampade Salotto\n"
        "- in view now: Matteo (browser)"
    )
    assert digest == expected


def test_person_events_capped_to_newest_six_chronological():
    events = [
        {"at": f"2026-09-17T{h:02d}:00:00+00:00", "kind": "person_entered", "subject": f"P{h}", "source": "browser"}
        for h in range(20)
    ]
    digest = build_digest(
        now=datetime(2026, 9, 17, 21, 0, tzinfo=timezone.utc),
        tz_name="UTC",
        last_ended_at="2026-09-16T19:58:00+00:00",
        perception_events=events,
        notifications=[],
        lights_on=None,
        present=[],
    )
    lines = digest.split("\n")
    bullet_lines = [line for line in lines if line.startswith("- ")]
    assert len(bullet_lines) == 6
    # newest 6 = hours 14..19, in chronological order
    expected_subjects = [f"P{h}" for h in range(14, 20)]
    for subject, line in zip(expected_subjects, bullet_lines):
        assert subject in line


def test_caps_600_chars_and_10_bullets():
    events = [
        {"at": f"2026-09-17T{h:02d}:00:00+00:00", "kind": "person_entered",
         "subject": "A" * 100, "source": "browser"}
        for h in range(6)
    ]
    notifications = [
        {"loop_name": f"loop{i}", "summary": "x" * 100, "created_at": f"2026-09-17T0{i}:00:00+00:00"}
        for i in range(3)
    ]
    digest = build_digest(
        now=datetime(2026, 9, 17, 21, 0, tzinfo=timezone.utc),
        tz_name="UTC",
        last_ended_at="2026-09-16T19:58:00+00:00",
        perception_events=events,
        notifications=notifications,
        lights_on=["L1", "L2", "L3", "L4", "L5"],
        present=[{"subject": "Matteo", "source": "browser"}],
    )
    assert len(digest) <= 600
    bullet_lines = [line for line in digest.split("\n") if line.startswith("- ")]
    assert len(bullet_lines) <= 10


def test_ago_minutes_under_an_hour():
    digest = build_digest(
        now=datetime(2026, 9, 16, 20, 28, tzinfo=timezone.utc),
        tz_name="Europe/Rome",
        last_ended_at="2026-09-16T19:58:00+00:00",
        perception_events=[{"at": "2026-09-16T20:14:00+00:00", "kind": "person_left",
                             "subject": "Matteo", "source": "browser"}],
        notifications=[],
        lights_on=None,
        present=[],
    )
    assert "(30 min ago)" in digest


def test_ago_days():
    digest = build_digest(
        now=datetime(2026, 9, 20, 19, 58, tzinfo=timezone.utc),
        tz_name="Europe/Rome",
        last_ended_at="2026-09-16T19:58:00+00:00",
        perception_events=[{"at": "2026-09-16T20:14:00+00:00", "kind": "person_left",
                             "subject": "Matteo", "source": "browser"}],
        notifications=[],
        lights_on=None,
        present=[],
    )
    assert "(4 days ago)" in digest


def test_no_lights_on_now_line_when_empty_list():
    digest = build_digest(
        now=datetime(2026, 9, 16, 20, 28, tzinfo=timezone.utc),
        tz_name="Europe/Rome",
        last_ended_at="2026-09-16T19:58:00+00:00",
        perception_events=[{"at": "2026-09-16T20:14:00+00:00", "kind": "person_left",
                             "subject": "Matteo", "source": "browser"}],
        notifications=[],
        lights_on=[],
        present=[],
    )
    assert "no lights on now" in digest


def test_lights_line_absent_when_none():
    digest = build_digest(
        now=datetime(2026, 9, 16, 20, 28, tzinfo=timezone.utc),
        tz_name="Europe/Rome",
        last_ended_at="2026-09-16T19:58:00+00:00",
        perception_events=[{"at": "2026-09-16T20:14:00+00:00", "kind": "person_left",
                             "subject": "Matteo", "source": "browser"}],
        notifications=[],
        lights_on=None,
        present=[],
    )
    assert "lights on now" not in digest
    assert "no lights on now" not in digest


# --- SessionState -----------------------------------------------------------

def test_session_state_defaults_on_missing_file(tmp_path):
    state = SessionState(tmp_path / "missing.json")
    assert state.read() == {
        "last_ended_at": None,
        "last_perception_id": 0,
        "last_notification_id": 0,
    }


def test_session_state_defaults_on_corrupt_file(tmp_path):
    path = tmp_path / "state.json"
    path.write_text("{not json", encoding="utf-8")
    state = SessionState(path)
    assert state.read() == {
        "last_ended_at": None,
        "last_perception_id": 0,
        "last_notification_id": 0,
    }


def test_session_state_write_read_round_trip(tmp_path):
    path = tmp_path / "state.json"
    state = SessionState(path)
    state.write(last_ended_at="2026-09-16T19:58:00+00:00", last_perception_id=42, last_notification_id=7)
    assert state.read() == {
        "last_ended_at": "2026-09-16T19:58:00+00:00",
        "last_perception_id": 42,
        "last_notification_id": 7,
    }


def test_session_state_file_mode_0600(tmp_path):
    path = tmp_path / "state.json"
    state = SessionState(path)
    state.write(last_ended_at="2026-09-16T19:58:00+00:00", last_perception_id=1, last_notification_id=1)
    mode = stat.S_IMODE(path.stat().st_mode)
    assert mode == 0o600
