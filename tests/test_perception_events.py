from richard.perception.detect import Detection
from richard.perception.events import KINDS, PerceptionEvent, PresenceLog, PresenceState

BOX = Detection(box=(0.3, 0.2, 0.6, 0.9), score=0.9)


def kinds(events):
    return [(e.kind, e.subject) for e in events]


def test_event_line_and_dict():
    e = PerceptionEvent(ts=12.5, source_id="browser", kind="identified", subject="matteo", confidence=0.91)
    assert e.line() == "face recognition guesses matteo is in frame; unverified, look to confirm (browser)"
    assert PerceptionEvent(1.0, "browser", "person_entered", "unknown").line() == "someone appeared in the camera frame (browser)"
    assert PerceptionEvent(1.0, "browser", "person_left", "matteo").line() == "matteo is no longer in the camera frame (browser)"
    assert PerceptionEvent(1.0, "browser", "stillness", "12.0").line() == "nothing has moved in the camera frame for 12.0 minutes (browser)"
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


def test_presence_switches_subject_after_two_consecutive_votes():
    state = PresenceState("browser", enter_debounce_s=0.0, leave_debounce_s=10.0)
    box = Detection(score=0.9, box=(0.1, 0.1, 0.5, 0.5))
    events = []
    t = 0.0
    for name in ["Matteo", "Matteo", "Matteo", "Anna", "Matteo", "Anna", "Anna", "Anna"]:
        t += 1.0
        events += [(e.kind, e.subject) for e in state.observe(t, [box], [name])]
    assert ("identified", "Matteo") in events
    assert ("identified", "Anna") in events
    assert events.count(("identified", "Anna")) == 1          # one flip, after the two consecutive Anna votes
    assert events.index(("identified", "Anna")) > events.index(("identified", "Matteo"))
    assert state.present()[0].subject == "Anna"


def test_non_consecutive_votes_do_not_flip_the_label():
    state = PresenceState("browser", enter_debounce_s=0.0, leave_debounce_s=10.0)
    box = Detection(score=0.9, box=(0.1, 0.1, 0.5, 0.5))
    events = []
    t = 0.0
    for name in ["Matteo", "Matteo", "Anna", "Matteo", "Anna"]:
        t += 1.0
        events += [(e.kind, e.subject) for e in state.observe(t, [box], [name])]
    assert events.count(("identified", "Matteo")) == 1
    assert ("identified", "Anna") not in events
    assert state.present()[0].subject == "Matteo"


def test_two_consecutive_votes_for_a_new_name_flip_exactly_once():
    state = PresenceState("browser", enter_debounce_s=0.0, leave_debounce_s=10.0)
    box = Detection(score=0.9, box=(0.1, 0.1, 0.5, 0.5))
    events = []
    t = 0.0
    for name in ["Matteo", "Matteo", "Anna", "Anna"]:
        t += 1.0
        events += [(e.kind, e.subject) for e in state.observe(t, [box], [name])]
    assert events.count(("identified", "Matteo")) == 1
    assert events.count(("identified", "Anna")) == 1
    assert state.present()[0].subject == "Anna"


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


def test_presence_log_last_id(tmp_path):
    log = PresenceLog(tmp_path / "perception.db")
    assert log.last_id() == 0
    log.append(PerceptionEvent(1.0, "browser", "person_entered", "unknown"))
    assert log.last_id() == 1
    log.append(PerceptionEvent(2.0, "browser", "identified", "matteo", 0.9))
    assert log.last_id() == 2
    log.close()


def test_unknown_person_is_never_reported_when_identity_is_off():
    state = PresenceState("browser", enter_debounce_s=0.0, unknown_after_s=5.0, report_unknown=False)
    state.observe(0.0, [BOX], [None])
    assert state.observe(6.0, [BOX], [None]) == []
    assert state.observe(30.0, [BOX], [None]) == []


def test_event_lines_read_as_camera_observations_not_facts():
    from richard.perception.events import PerceptionEvent

    def line(kind, subject=""):
        return PerceptionEvent(0.0, "browser", kind, subject).line()

    assert line("person_entered", "Matteo") == "Matteo appeared in the camera frame (browser)"
    assert line("person_entered") == "someone appeared in the camera frame (browser)"
    assert line("person_left", "Matteo") == "Matteo is no longer in the camera frame (browser)"
    assert line("identified", "Anna") == "face recognition guesses Anna is in frame; unverified, look to confirm (browser)"
    assert line("unknown_person") == "a person the face recognition does not know is in the camera frame (browser)"
    assert line("scene_changed") == "the camera view changed (browser)"
    assert line("motion_after_stillness") == "movement in the camera frame after a long stillness (browser)"
    assert line("stillness", "12") == "nothing has moved in the camera frame for 12 minutes (browser)"
