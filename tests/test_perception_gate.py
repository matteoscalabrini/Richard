from datetime import datetime

import pytest

from richard.perception.events import PerceptionEvent
from richard.perception.gate import Gate, GatePolicy, in_quiet_hours, parse_quiet_hours


def ev(kind, subject="unknown", ts=0.0):
    return PerceptionEvent(ts, "browser", kind, subject)


class Clock:
    def __init__(self, t=0.0):
        self.t = t

    def __call__(self):
        return self.t


def test_parse_quiet_hours():
    assert parse_quiet_hours("") is None
    assert parse_quiet_hours("23:00-07:30") == ((23, 0), (7, 30))
    with pytest.raises(ValueError):
        parse_quiet_hours("late-early")


@pytest.mark.parametrize("text,hour,minute,quiet", [
    ("23:00-07:30", 23, 30, True), ("23:00-07:30", 3, 0, True), ("23:00-07:30", 7, 29, True),
    ("23:00-07:30", 7, 30, False), ("23:00-07:30", 12, 0, False),
    ("13:00-14:00", 13, 30, True), ("13:00-14:00", 14, 0, False), ("", 3, 0, False),
])
def test_in_quiet_hours(text, hour, minute, quiet):
    policy = GatePolicy(quiet_hours=text)
    assert in_quiet_hours(policy, datetime(2026, 9, 9, hour, minute)) is quiet


def test_disabled_gate_admits_nothing():
    gate = Gate(GatePolicy(enabled=False))
    assert gate.admit([ev("person_entered")]) == []
    assert gate.reasons == [("person_entered", "disabled")]


def test_cooldown_per_kind_and_subject():
    clock = Clock(0.0)
    gate = Gate(GatePolicy(cooldown_s=120.0), clock=clock, wall=lambda: datetime(2026, 9, 9, 12, 0))
    assert gate.admit([ev("identified", "matteo")]) == [ev("identified", "matteo")]
    clock.t = 60.0
    assert gate.admit([ev("identified", "matteo")]) == []
    assert gate.reasons == [("identified", "cooldown")]
    assert gate.admit([ev("identified", "guest")]) == [ev("identified", "guest")]  # other subject
    clock.t = 121.0
    assert gate.admit([ev("identified", "matteo")]) == [ev("identified", "matteo")]


def test_quiet_hours_drop_everything_but_still_update_nothing():
    gate = Gate(GatePolicy(quiet_hours="23:00-07:30"), wall=lambda: datetime(2026, 9, 9, 2, 0))
    assert gate.admit([ev("person_entered"), ev("scene_changed")]) == []
    assert gate.reasons == [("person_entered", "quiet_hours"), ("scene_changed", "quiet_hours")]
