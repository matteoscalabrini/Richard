import json

from richard.perception.camera import CameraProvider, PresenceProvider
from richard.providers.base import ToolResult


class FakeService:
    def __init__(self, live=("browser",)):
        self._live = list(live)
        self.calls = []
        self._presence = [{"source": "browser", "subject": "matteo", "since": 5.0}]

        class Log:
            def last_seen(self, name):
                return {"at": "2026-09-09T18:00:00+00:00", "kind": "person_left", "source": "browser"} if name == "matteo" else None
        self.log = Log()

    def live_sources(self):
        return self._live

    def snapshot(self, source_id=None, detail="low", region=None):
        self.calls.append((source_id, detail, region))
        if not self._live:
            return None
        if region == "nowhere":
            raise ValueError("region must be left|right|centre|top|bottom or x0,y0,x1,y1")
        return b"\xff\xd8jpeg", {"source": "browser", "detail": detail, "region": region, "width": 800, "height": 450}

    def presence(self):
        return self._presence


def test_camera_schema_only_when_a_source_is_live():
    assert CameraProvider(FakeService(live=())).schemas() == []
    (schema,) = CameraProvider(FakeService()).schemas()
    fn = schema["function"]
    assert fn["name"] == "camera" and fn["parameters"]["required"] == ["question"]
    assert fn["parameters"]["properties"]["detail"]["enum"] == ["low", "high"]
    assert "region" in fn["parameters"]["properties"]


def test_camera_execute_returns_the_frame_as_a_tool_result():
    svc = FakeService()
    out = CameraProvider(svc).execute("camera", {"question": "what", "detail": "high", "region": "left"})
    assert isinstance(out, ToolResult)
    assert json.loads(out.text) == {"image_attached": True, "image_width": 800, "image_height": 450, "source": "browser", "detail": "high", "region": "left"}
    assert out.images == ("data:image/jpeg;base64,/9hqcGVn",)  # base64 of ff d8 "jpeg"
    assert svc.calls == [(None, "high", "left")]


def test_camera_execute_reports_no_frame_and_bad_region_as_text():
    assert "No camera is streaming" in CameraProvider(FakeService(live=())).execute("camera", {"question": "q"})
    out = CameraProvider(FakeService()).execute("camera", {"question": "q", "region": "nowhere"})
    assert isinstance(out, str) and "region" in out


def test_presence_tools():
    p = PresenceProvider(FakeService())
    assert [s["function"]["name"] for s in p.schemas()] == ["who_is_here", "last_seen"]
    assert p.execute("who_is_here", {}) == "Present now: matteo (browser)."
    assert "matteo" in p.execute("last_seen", {"name": "matteo"}) and "2026-09-09" in p.execute("last_seen", {"name": "matteo"})
    assert "never" in p.execute("last_seen", {"name": "ghost"}).lower()
    svc = FakeService()
    svc._presence = []
    assert PresenceProvider(svc).execute("who_is_here", {}) == "Nobody is in view right now."
