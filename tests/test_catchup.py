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
        "[catch-up] Richard's own notes from while nobody was talking (last conversation ended "
        "2026-09-16 21:58, 11 h ago). Not a message from the user and not news. Reply only to "
        "what the user says next; mention any of this only if asked or directly relevant.\n"
        "- 22:14 Matteo is no longer in the camera frame (browser)\n"
        "- 07:40 someone appeared in the camera frame (browser)\n"
        "- camera view changed 6 times; 2 still periods\n"
        '- loop "kitchen light" 07:55: the light was left on overnight\n'
        "- lights on now: Lampada Scrivania, Lampade Salotto\n"
        "- camera currently shows: Matteo (browser)"
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
    assert "30 min ago" in digest


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
    assert "4 days ago" in digest


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


# --- CatchUp -----------------------------------------------------------

import logging
import threading


class FakeLog:
    def __init__(self, events, last_id=0):
        self._events = events
        self._last_id = last_id

    def recent(self, since_id=0, limit=100):
        self.recent_call = (since_id, limit)
        return [e for e in self._events if e["id"] > since_id][:limit]

    def last_id(self):
        return self._last_id


class FakePerceptionService:
    def __init__(self, events, present=None, last_id=0):
        self.log = FakeLog(events, last_id=last_id)
        self._present = present or []

    def presence(self):
        return self._present


class FakeNotification:
    def __init__(self, id, loop_name, summary, created_at):
        self.id = id
        self.loop_name = loop_name
        self.summary = summary
        self.created_at = created_at


class FakeStore:
    def __init__(self, notes, last_id=0):
        self._notes = notes
        self._last_id = last_id

    def notifications(self, *, limit=50):
        self.notifications_call = limit
        return self._notes

    def last_notification_id(self):
        return self._last_id


class FakeEntity:
    def __init__(self, entity_id, state, attributes=None):
        self.entity_id = entity_id
        self.state = state
        self.attributes = attributes or {}
        self.domain = entity_id.split(".", 1)[0]


class FakeHAClient:
    def __init__(self, entities):
        self._entities = entities

    def list_entities(self):
        return self._entities


class RaisingHAClient:
    def list_entities(self):
        raise RuntimeError("boom")


class FakeConversation:
    def __init__(self, messages=None):
        self._messages = messages or []

    def history(self):
        return self._messages


class FakeSession:
    def __init__(self, history=None):
        self.background = None
        self.event = threading.Event()
        self.conversation = FakeConversation(history)

    def add_background(self, text):
        self.background = text
        self.event.set()


def _state(tmp_path, **overrides):
    from richard.catchup import SessionState

    state = SessionState(tmp_path / "state.json")
    defaults = {"last_ended_at": "2026-09-16T19:58:00+00:00", "last_perception_id": 0, "last_notification_id": 0}
    defaults.update(overrides)
    state.write(**defaults)
    return state


def test_catchup_digest_gathers_all_sources(tmp_path):
    from richard.catchup import CatchUp

    state = _state(tmp_path, last_perception_id=5, last_notification_id=2)
    events = [
        {"id": 6, "at": "2026-09-17T05:40:00+00:00", "kind": "person_entered", "subject": "Matteo", "source": "browser"},
    ]
    notes = [
        FakeNotification(1, "old loop", "should be excluded", "2026-09-17T00:00:00+00:00"),
        FakeNotification(3, "kitchen light", "the light was left on overnight", "2026-09-17T05:55:00+00:00"),
    ]
    entities = [
        FakeEntity("light.desk", "on", {"friendly_name": "Lampada Scrivania"}),
        FakeEntity("light.kitchen", "off"),
        FakeEntity("switch.fan", "on"),
    ]
    service = FakePerceptionService(events, present=[{"subject": "Matteo", "source": "browser"}], last_id=6)
    store = FakeStore(notes, last_id=3)
    ha_client = FakeHAClient(entities)

    catchup = CatchUp(
        state, tz_name="Europe/Rome", perception_service=service, control_store=store, ha_client=ha_client,
        now=lambda tz: datetime(2026, 9, 17, 6, 58, tzinfo=timezone.utc),
    )
    digest = catchup.digest()

    assert service.log.recent_call == (5, 200)
    assert store.notifications_call == 50
    assert "Matteo" in digest
    assert "kitchen light" in digest
    assert "old loop" not in digest  # id <= last_notification_id, excluded
    assert "Lampada Scrivania" in digest
    assert "switch.fan" not in digest  # not domain light
    assert "camera currently shows: Matteo (browser)" in digest


def test_catchup_digest_skips_absent_sources(tmp_path):
    from richard.catchup import CatchUp

    state = _state(tmp_path)
    catchup = CatchUp(
        state, tz_name="Europe/Rome", now=lambda tz: datetime(2026, 9, 17, 6, 58, tzinfo=timezone.utc),
    )
    # No sources at all -> no perception events, no notifications, no lights, no presence
    # last_ended_at is set but nothing happened -> None
    assert catchup.digest() is None


def test_catchup_digest_ha_client_raising_still_builds_digest(tmp_path):
    from richard.catchup import CatchUp

    state = _state(tmp_path)
    events = [{"id": 1, "at": "2026-09-17T05:40:00+00:00", "kind": "person_entered", "subject": "Matteo",
               "source": "browser"}]
    service = FakePerceptionService(events, last_id=1)
    catchup = CatchUp(
        state, tz_name="Europe/Rome", perception_service=service, ha_client=RaisingHAClient(),
        now=lambda tz: datetime(2026, 9, 17, 6, 58, tzinfo=timezone.utc),
    )
    digest = catchup.digest()
    assert digest is not None
    assert "lights on now" not in digest
    assert "no lights on now" not in digest


def test_catchup_mark_ended_writes_newest_ids(tmp_path):
    from richard.catchup import CatchUp

    state = _state(tmp_path)
    service = FakePerceptionService([], last_id=42)
    store = FakeStore([], last_id=7)
    catchup = CatchUp(
        state, tz_name="Europe/Rome", perception_service=service, control_store=store,
        now=lambda tz: datetime(2026, 9, 17, 6, 58, tzinfo=timezone.utc),
    )
    catchup.mark_ended()
    data = state.read()
    assert data["last_ended_at"] == "2026-09-17T06:58:00+00:00"
    assert data["last_perception_id"] == 42
    assert data["last_notification_id"] == 7


def test_catchup_mark_ended_defaults_to_zero_without_sources(tmp_path):
    from richard.catchup import CatchUp

    state = _state(tmp_path)
    catchup = CatchUp(
        state, tz_name="Europe/Rome",
        now=lambda tz: datetime(2026, 9, 17, 6, 58, tzinfo=timezone.utc),
    )
    catchup.mark_ended()
    data = state.read()
    assert data["last_perception_id"] == 0
    assert data["last_notification_id"] == 0


def test_catchup_mark_ended_swallows_exception_and_warns(tmp_path, caplog):
    from richard.catchup import CatchUp

    class BoomState:
        def write(self, **kwargs):
            raise RuntimeError("disk full")

    catchup = CatchUp(
        BoomState(), tz_name="Europe/Rome",
        now=lambda tz: datetime(2026, 9, 17, 6, 58, tzinfo=timezone.utc),
    )
    with caplog.at_level(logging.WARNING, logger="richard.catchup"):
        catchup.mark_ended()  # must not raise
    assert any("catch-up digest failed" in r.message for r in caplog.records)


def test_catchup_attach_queues_digest_on_session(tmp_path, caplog):
    from richard.catchup import CatchUp

    state = _state(tmp_path)
    events = [{"id": 1, "at": "2026-09-17T05:40:00+00:00", "kind": "person_entered", "subject": "Matteo",
               "source": "browser"}]
    service = FakePerceptionService(events, last_id=1)
    catchup = CatchUp(
        state, tz_name="Europe/Rome", perception_service=service,
        now=lambda tz: datetime(2026, 9, 17, 6, 58, tzinfo=timezone.utc),
    )
    session = FakeSession()
    with caplog.at_level(logging.INFO, logger="richard.catchup"):
        catchup.attach(session)
        assert session.event.wait(timeout=2.0)
    assert session.background is not None
    assert "Matteo" in session.background
    assert any("catch-up digest queued" in r.message for r in caplog.records)


def test_catchup_attach_swallows_exception_and_warns(tmp_path, caplog):
    from richard.catchup import CatchUp

    class BoomLog:
        def recent(self, since_id=0, limit=100):
            raise RuntimeError("db locked")

        def last_id(self):
            return 0

    class BoomService:
        log = BoomLog()

        def presence(self):
            return []

    state = _state(tmp_path)
    catchup = CatchUp(state, tz_name="Europe/Rome", perception_service=BoomService(),
                       now=lambda tz: datetime(2026, 9, 17, 6, 58, tzinfo=timezone.utc))
    session = FakeSession()
    with caplog.at_level(logging.WARNING, logger="richard.catchup"):
        catchup.attach(session)
        # give the daemon thread time to run and log
        import time as _time
        for _ in range(50):
            if any("catch-up digest failed" in r.message for r in caplog.records):
                break
            _time.sleep(0.05)
    assert session.background is None
    assert any("catch-up digest failed" in r.message for r in caplog.records)


def test_catchup_attach_skips_when_conversation_already_started(tmp_path, caplog):
    from richard.catchup import CatchUp

    state = _state(tmp_path)
    events = [{"id": 1, "at": "2026-09-17T05:40:00+00:00", "kind": "person_entered", "subject": "Matteo",
               "source": "browser"}]
    service = FakePerceptionService(events, last_id=1)
    catchup = CatchUp(
        state, tz_name="Europe/Rome", perception_service=service,
        now=lambda tz: datetime(2026, 9, 17, 6, 58, tzinfo=timezone.utc),
    )
    session = FakeSession(history=[object()])  # a message already in the conversation
    with caplog.at_level(logging.INFO, logger="richard.catchup"):
        thread = catchup.attach(session)
        thread.join(timeout=2.0)
    assert session.background is None
    assert any("catch-up digest skipped: conversation already started" in r.message
                for r in caplog.records)


def test_catchup_mark_ended_keeps_previous_perception_id_without_source(tmp_path):
    from richard.catchup import CatchUp

    state = _state(tmp_path, last_perception_id=99, last_notification_id=13)
    catchup = CatchUp(
        state, tz_name="Europe/Rome",
        now=lambda tz: datetime(2026, 9, 17, 6, 58, tzinfo=timezone.utc),
    )
    catchup.mark_ended()
    data = state.read()
    assert data["last_perception_id"] == 99
    assert data["last_notification_id"] == 13
