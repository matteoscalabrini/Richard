import io

import numpy as np
import pytest

pytest.importorskip("PIL")
from PIL import Image  # noqa: E402

from richard.perception.detect import Detection  # noqa: E402
from richard.perception.faces import FaceMatch  # noqa: E402
from richard.perception.pipeline import PerceptionService, Settings  # noqa: E402


def _jpeg(color, w=64, h=36):
    buf = io.BytesIO()
    Image.new("RGB", (w, h), color).save(buf, format="JPEG")
    return buf.getvalue()


class Clock:
    def __init__(self, t=1000.0):
        self.t = t

    def __call__(self):
        return self.t


class ScriptedPersons:
    """Returns the next scripted detection list on each call; sticks on the last."""

    def __init__(self, script):
        self.script = list(script)
        self.calls = 0

    def detect(self, rgb):
        self.calls += 1
        if len(self.script) > 1:
            return self.script.pop(0)
        return self.script[0] if self.script else []


class ScriptedFaces:
    def __init__(self, name):
        self.name = name

    def identify(self, rgb):
        return [FaceMatch(box=(0.4, 0.2, 0.6, 0.5), name=self.name, score=0.9)]


PERSON = [Detection(box=(0.3, 0.1, 0.7, 0.9), score=0.9)]


def test_settings_from_table_and_thresholds():
    s = Settings.from_table({"sensitivity": 50, "quiet_hours": "23:00-07:30", "stream_fps": 4})
    assert s.motion_threshold == pytest.approx(0.06) and s.person_threshold == pytest.approx(0.55)
    assert s.stream_fps == 4 and s.quiet_hours == "23:00-07:30" and s.identity_enabled is False
    assert Settings.from_table({}).cooldown_s == 120.0


def test_service_emits_entered_identified_left_through_gate_and_sinks(tmp_path):
    clock = Clock()
    settings = Settings.from_table({"enter_debounce_s": 1.0, "leave_debounce_s": 3.0, "cooldown_s": 0.0, "identity_enabled": True})
    svc = PerceptionService(settings, data_dir=tmp_path, clock=clock,
                            person_detector=ScriptedPersons([PERSON]), identifier=ScriptedFaces("matteo"))
    seen = []
    svc.add_sink(seen.append)
    for t in (0.0, 0.5, 1.5, 2.5, 3.5):  # face stage runs once per second while unresolved
        clock.t = 1000.0 + t
        svc.push_frame("browser", _jpeg((10, 10, 10)))
        svc.process("browser")
    kinds = [(e.kind, e.subject) for e in seen]
    assert kinds[:2] == [("person_entered", "unknown"), ("identified", "matteo")]
    assert svc.presence() == [{"source": "browser", "subject": "matteo", "since": 1000.0}]
    svc._pipelines["browser"]._person_detector = ScriptedPersons([[]])
    for t in (4.0, 5.0, 8.0):
        clock.t = 1000.0 + t
        svc.push_frame("browser", _jpeg((10, 10, 10)))
        svc.process("browser")
    assert [(e.kind, e.subject) for e in seen][-1] == ("person_left", "matteo")
    assert [r["kind"] for r in svc.recent_events()] == ["person_entered", "identified", "person_left"]
    assert svc.log.last_seen("matteo")["kind"] == "person_left"


def test_context_sink_takes_events_instead_of_loop_sinks(tmp_path):
    clock = Clock()
    settings = Settings.from_table({"enter_debounce_s": 0.0, "cooldown_s": 0.0})
    svc = PerceptionService(settings, data_dir=tmp_path, clock=clock, person_detector=ScriptedPersons([PERSON]))
    loop_events = []
    lines = []
    stop = svc.event_source(loop_events.append)
    svc.set_context_sink(lambda line, wake=False: lines.append((line, wake)) or True)
    svc.push_frame("browser", _jpeg((0, 0, 0)))
    svc.process("browser")
    assert lines == [("someone entered (browser)", True)]  # an arrival wakes the session
    assert loop_events == []
    svc.set_context_sink(lambda line, wake=False: False)
    svc._pipelines["browser"]._person_detector = ScriptedPersons([[]])
    clock.t = 1020.0
    svc.push_frame("browser", _jpeg((0, 0, 0)))
    svc.process("browser")
    assert [e.kind for e in loop_events] == ["person_left"]
    assert loop_events[0].target == "perception:person_present"
    stop()


def test_snapshot_detail_and_region(tmp_path):
    svc = PerceptionService(Settings.from_table({}), data_dir=tmp_path, person_detector=ScriptedPersons([[]]))
    assert svc.snapshot() is None
    svc.push_frame("browser", _jpeg((200, 30, 30), w=1600, h=900))
    jpeg, meta = svc.snapshot(detail="low")
    assert meta["width"] == 800 and meta["height"] == 450 and meta["source"] == "browser"
    _, meta_high = svc.snapshot(detail="high")
    assert meta_high["width"] == 1600
    _, meta_region = svc.snapshot(region="left")  # 800x900 crop, bounded to an 800 px long edge
    assert meta_region["width"] == 711 and meta_region["height"] == 800 and meta_region["region"] == "left"
    with pytest.raises(ValueError):
        svc.snapshot(region="nowhere")


def test_status_reports_sources_and_staleness(tmp_path):
    clock = Clock()
    svc = PerceptionService(Settings.from_table({"stale_s": 5}), data_dir=tmp_path, clock=clock, person_detector=ScriptedPersons([[]]))
    assert svc.status()["sources"] == []
    svc.push_frame("browser", _jpeg((0, 0, 0)))
    assert svc.status()["sources"] == [{"id": "browser", "stale": False, "frames": 1}]
    clock.t += 10
    assert svc.status()["sources"][0]["stale"] is True
    assert svc.live_sources() == []


def test_enrol_uses_the_identifier_and_the_gallery(tmp_path):
    class Enroller:
        def identify(self, rgb):
            return [FaceMatch(box=(0.1, 0.1, 0.5, 0.5), name=None, score=0.0)]

        def embed_box(self, rgb, box):
            v = np.zeros(512, dtype=np.float32); v[0] = 1.0
            return v

    svc = PerceptionService(Settings.from_table({"identity_enabled": True}), data_dir=tmp_path,
                            person_detector=ScriptedPersons([[]]), identifier=Enroller())
    assert svc.enrol("matteo", [_jpeg((1, 2, 3)), _jpeg((3, 2, 1))]) == 2
    assert [p["name"] for p in svc.gallery.list()] == ["matteo"]
