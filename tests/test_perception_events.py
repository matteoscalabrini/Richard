from richard.perception.detect import Detection
from richard.perception.events import KINDS, PerceptionEvent, PresenceLog, PresenceState

BOX = Detection(box=(0.3, 0.2, 0.6, 0.9), score=0.9)


def kinds(events):
    return [(e.kind, e.subject) for e in events]


def test_event_line_and_dict():
    e = PerceptionEvent(ts=12.5, source_id="browser", kind="identified", subject="matteo", confidence=0.91)
    assert e.line() == "matteo recognised (browser)"
    assert PerceptionEvent(1.0, "browser", "person_entered", "unknown").line() == "someone entered (browser)"
    assert PerceptionEvent(1.0, "browser", "person_left", "matteo").line() == "matteo left (browser)"
    assert PerceptionEvent(1.0, "browser", "stillness", "12.0").line() == "nothing has moved for 12.0 minutes (browser)"
    assert e.to_dict()["kind"] == "identified" and e.to_dict()["confidence"] == 0.91
    assert e.kind in KINDS


def test_someone_must_persist_before_entering():
    state = PresenceState("browser", enter_debounce_s=2.0, leave_debounce_s=10.0)
    assert state.observe(0.0, [BOX], [None]) == []
    assert state.observe(1.0, [BOX], [None]) == []
    events = state.observe(2.5, [BOX], [None])
    assert kinds(events) == [("person_entered", "unknown")]
    assert [p.subject for p in state.present()] == ["unknown"]
    assert state.present()[0].since == 0.0
    assert state.observe(3.0, [BOX], [None]) == []  # no repeats


def test_identity_needs_two_agreeing_matches_then_names_the_presence():
    state = PresenceState("browser", enter_debounce_s=0.0)
    state.observe(0.0, [BOX], [None])
    assert kinds(state.observe(0.5, [BOX], ["matteo"])) == []
    events = state.observe(1.0, [BOX], ["matteo"])
    assert kinds(events) == [("identified", "matteo")]
    assert [p.subject for p in state.present()] == ["matteo"]
    assert state.observe(1.5, [BOX], ["matteo"]) == []


def test_unknown_person_is_reported_once_after_identity_fails_for_a_while():
    state = PresenceState("browser", enter_debounce_s=0.0, unknown_after_s=5.0)
    state.observe(0.0, [BOX], [None])
    for t in (1.0, 3.0):
        assert state.observe(t, [BOX], [None]) == []
    assert kinds(state.observe(5.5, [BOX], [None])) == [("unknown_person", "unknown")]
    assert state.observe(7.0, [BOX], [None]) == []


def test_leaving_needs_a_quiet_gap():
    state = PresenceState("browser", enter_debounce_s=0.0, leave_debounce_s=10.0)
    state.observe(0.0, [BOX], ["matteo"])
    state.observe(0.5, [BOX], ["matteo"])
    assert state.observe(5.0, [], []) == []       # briefly out of frame
    assert kinds(state.observe(8.0, [BOX], ["matteo"])) == []  # back: no re-entry event
    assert state.observe(15.0, [], []) == []          # 7 s gone: not yet
    events = state.observe(20.0, [], [])              # 12 s gone: left
    assert kinds(events) == [("person_left", "matteo")]
    assert state.present() == []


def test_presence_log_round_trip(tmp_path):
    log = PresenceLog(tmp_path / "perception.db")
    e1 = PerceptionEvent(1.0, "browser", "person_entered", "unknown")
    e2 = PerceptionEvent(2.0, "browser", "identified", "matteo", 0.9)
    assert log.append(e1) == 1 and log.append(e2, thumbnail=b"\xff\xd8") == 2
    rows = log.recent()
    assert [r["kind"] for r in rows] == ["person_entered", "identified"]
    assert rows[1]["has_thumbnail"] is True and rows[0]["has_thumbnail"] is False
    assert log.recent(since_id=1) == [rows[1]]
    assert log.last_seen("matteo")["id"] == 2 and log.last_seen("nobody") is None
    assert log.thumbnail(2) == b"\xff\xd8"
    log.close()
