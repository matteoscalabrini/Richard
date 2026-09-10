import base64
import threading
import time

import pytest

from richard.brain.completion import StreamEvent, ToolCall
from richard.config import Config, Personality
from richard.conversation import Conversation
from richard.engine import Engine
from richard.errors import BrainRejectedInput
from richard.perception.camera import CameraProvider
from richard.providers.base import ToolResult
from richard.realtime import events
from richard.realtime.registry import SessionRegistry
from richard.realtime.session import RealtimeSession


FRAME = b"\x00" * 1024
IMAGE = "data:image/jpeg;base64,/9j/4AAQ"
CAMERA_SPEC = {
    "type": "function",
    "name": "camera",
    "description": "look",
    "parameters": {"type": "object", "properties": {}},
}


def wait_until(predicate, timeout=5.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(0.001)
    raise AssertionError("condition did not become true")


class NullDetector:
    in_speech = False

    def feed(self, frame):
        return []

    def collected(self):
        return b""


class NullTranscriber:
    def partial(self, pcm):
        return ""

    def final(self, pcm):
        return ""


class RecordingTTS:
    samplerate = 24000

    def __init__(self):
        self.spoken = []

    def synth(self, text):
        self.spoken.append(text)
        return text.encode()


class ScriptBrain:
    def __init__(self, scripts):
        self.scripts = list(scripts)
        self.calls = []

    def stream(self, messages, tools=None):
        self.calls.append(list(messages))
        script = self.scripts.pop(0)
        error = script.get("error")
        if error:
            raise error
        for delta in script.get("deltas", []):
            yield StreamEvent(delta=delta)
        yield StreamEvent(tool_calls=script.get("tool_calls", []), done=True)

    def complete(self, *args, **kwargs):
        raise AssertionError("streaming tests must not call complete")


class NoTools:
    def schemas(self):
        return []

    def execute(self, name, arguments):
        raise AssertionError("no tools")

    def context(self):
        return None


def make_session(engine, *, detector=None, transcriber=None, tts=None, **kwargs):
    emitted = []
    session = RealtimeSession(
        engine=engine,
        transcriber=transcriber or NullTranscriber(),
        tts=tts or RecordingTTS(),
        detector=detector or NullDetector(),
        emit=emitted.append,
        **kwargs,
    )
    return session, emitted


def test_engine_observer_precedes_blocking_brain_and_tool_work_and_reports_tool_images():
    activities = []
    tool_entered = threading.Event()
    tool_release = threading.Event()

    class Provider:
        def schemas(self):
            return [{"type": "function", "function": {"name": "camera", "parameters": {}}}]

        def context(self):
            return None

        def execute(self, name, arguments):
            assert activities[-1] == "tool"
            tool_entered.set()
            assert tool_release.wait(5)
            return ToolResult("image attached", images=(IMAGE,))

    brain = ScriptBrain([
        {"tool_calls": [ToolCall(id="look", name="camera", arguments={})]},
        {"deltas": ["A cup."]},
    ])
    conversation = Conversation()
    conversation.add_user("look")
    output = []
    worker = threading.Thread(
        target=lambda: output.extend(
            Engine(brain, [Provider()], Personality()).respond_streaming(
                conversation, observer=activities.append
            )
        )
    )
    worker.start()
    assert tool_entered.wait(5)
    assert activities == ["thinking", "tool"]
    tool_release.set()
    worker.join(5)
    assert output == ["A cup."]
    assert activities == ["thinking", "tool", "vision"]


def test_engine_reports_thinking_before_entering_a_blocking_brain_call():
    activities = []
    entered = threading.Event()
    release = threading.Event()

    class BlockingBrain:
        def stream(self, messages, tools=None):
            assert activities == ["thinking"]
            entered.set()
            assert release.wait(5)
            yield StreamEvent(delta="Ready.")
            yield StreamEvent(done=True)

    conversation = Conversation()
    conversation.add_user("hello")
    output = []
    worker = threading.Thread(
        target=lambda: output.extend(
            Engine(BlockingBrain(), [NoTools()], Personality()).respond_streaming(
                conversation, observer=activities.append
            )
        )
    )
    worker.start()
    assert entered.wait(5)
    release.set()
    worker.join(5)
    assert output == ["Ready."]


def test_engine_serves_replaceable_visual_observation_without_growing_history():
    brain = ScriptBrain([{"deltas": ["Hello."]}, {"deltas": ["Still here."]}])
    engine = Engine(brain, [NoTools()], Personality())
    conversation = Conversation()
    conversation.add_user("hello")
    baseline = len(conversation.history())
    observation = [
        {"type": "text", "text": "Current camera observation; capture age 0.25 seconds. Observe it, do not assume a description."},
        {"type": "image_url", "image_url": {"url": IMAGE}},
    ]
    conversation.set_observation(observation)
    phases = []
    assert "".join(engine.respond_streaming(conversation, observer=phases.append)) == "Hello."
    assert brain.calls[0][-1] == {"role": "user", "content": observation}
    assert phases == ["vision"]
    assert len(conversation.history()) == baseline

    replacement = [{"type": "text", "text": "new"}, {"type": "image_url", "image_url": {"url": IMAGE}}]
    conversation.set_observation(replacement)
    list(engine.respond_streaming(conversation))
    assert brain.calls[1][-1]["content"] == replacement
    assert len(conversation.history()) == baseline
    conversation.set_observation(None)
    assert conversation.observation is None


def test_session_activity_metadata_and_logical_id_survive_client_camera_continuation():
    brain = ScriptBrain([
        {"tool_calls": [ToolCall(id="look", name="camera", arguments={})]},
        {"deltas": ["A blue mug."]},
    ])
    session, emitted = make_session(Engine(brain, [NoTools()], Personality()))
    try:
        session.update({"tools": [CAMERA_SPEC]})
        session.create_item({"kind": "message", "content": "what is this?"})
        session.create_response()
        wait_until(lambda: len([e for e in emitted if e["type"] == "response.done"]) == 1)
        session.create_item({"kind": "function_call_output", "call_id": "look", "output": "image attached"})
        session.create_item({"kind": "message", "content": [{"type": "image_url", "image_url": {"url": IMAGE}}]})
        session.create_response()
        wait_until(lambda: len([e for e in emitted if e["type"] == "response.done"]) == 2)

        created = [e["response"] for e in emitted if e["type"] == "response.created"]
        assert len(created) == 2
        assert created[0]["turn_id"] == created[1]["turn_id"]
        assert created[0]["unsolicited"] is False
        second_id = created[1]["id"]
        second = [e for e in emitted if e.get("response_id") == second_id]
        assert [e["phase"] for e in second if e["type"] == "response.activity"][:1] == ["vision"]
        assert [e["type"] for e in second].index("response.activity") < [e["type"] for e in second].index("response.audio.delta")
        answer = next(i for i, e in enumerate(second) if e.get("phase") == "answer")
        assert answer + 1 == next(i for i, e in enumerate(second) if e["type"] == "response.audio.delta")
    finally:
        session.close()


def test_session_emits_error_activity_before_image_failure_fallback_audio():
    brain = ScriptBrain([{"error": BrainRejectedInput("bad image")}])
    session, emitted = make_session(Engine(brain, [NoTools()], Personality()))
    try:
        session.create_item({"kind": "message", "content": [{"type": "image_url", "image_url": {"url": IMAGE}}]})
        session.create_response()
        wait_until(lambda: any(e["type"] == "response.done" for e in emitted))
        response_id = next(e["response"]["id"] for e in emitted if e["type"] == "response.created")
        scoped = [e for e in emitted if e.get("response_id") == response_id or e["type"] == "error"]
        assert [e.get("phase") for e in scoped if e["type"] == "response.activity"][:2] == ["vision", "error"]
        assert next(i for i, e in enumerate(scoped) if e.get("phase") == "error") < next(
            i for i, e in enumerate(scoped) if e["type"] == "error"
        )
    finally:
        session.close()


def test_session_replaces_or_clears_source_observation_without_persisting_it():
    shots = [
        (b"jpeg", {"source": "browser-alpha", "age_s": 0.25, "width": 1, "height": 1}),
        None,
    ]
    brain = ScriptBrain([{"deltas": ["First."]}, {"deltas": ["Second."]}])
    session, emitted = make_session(
        Engine(brain, [NoTools()], Personality()),
        observation=lambda source_id: shots.pop(0),
    )
    try:
        session.update({"source_id": "browser-alpha", "visual_context": True})
        for index, text in enumerate(("hello", "again"), start=1):
            session.create_item({"kind": "message", "content": text})
            session.create_response()
            wait_until(lambda: len([e for e in emitted if e["type"] == "response.done"]) == index)
        first_observation = brain.calls[0][-1]["content"]
        assert first_observation[0]["type"] == "text" and "0.25 seconds" in first_observation[0]["text"]
        assert "active conversation or action" in first_observation[0]["text"]
        assert "unless the user requested a description" in first_observation[0]["text"]
        assert first_observation[1]["image_url"]["url"].startswith("data:image/jpeg;base64,")
        assert all(
            not any(part.get("type") == "image_url" for part in (message.get("content") or []) if isinstance(part, dict))
            for message in brain.calls[1]
            if isinstance(message.get("content"), list)
        )
        assert all(not isinstance(message.content, list) for message in session.conversation.history())
    finally:
        session.close()


def test_blocked_old_tool_records_factual_result_before_new_input_and_cannot_emit_audio():
    entered = threading.Event()
    release = threading.Event()

    class Provider:
        def schemas(self):
            return [{"type": "function", "function": {"name": "switch", "parameters": {}}}]

        def context(self):
            return None

        def execute(self, name, arguments):
            entered.set()
            assert release.wait(5)
            return "switched"

    brain = ScriptBrain([
        {"tool_calls": [ToolCall(id="old-tool", name="switch", arguments={})]},
        {"deltas": ["Old answer."]},
        {"deltas": ["New answer."]},
    ])
    tts = RecordingTTS()
    session, emitted = make_session(Engine(brain, [Provider()], Personality()), tts=tts)
    try:
        session.create_item({"kind": "message", "content": "old request"})
        session.create_response()
        assert entered.wait(5)
        session.create_item({"kind": "message", "content": "new request"})
        session.create_response()
        release.set()
        wait_until(lambda: any("New answer" in text for text in tts.spoken))
        history = [m.to_chat() for m in session.conversation.history()]
        assert [m["role"] for m in history[:4]] == ["user", "assistant", "tool", "user"]
        assert history[2]["tool_call_id"] == "old-tool" and history[2]["content"] == "switched"
        assert history[3]["content"] == "new request"
        assert not any("Old answer" in text for text in tts.spoken)
    finally:
        session.close()


def test_new_typed_turn_replaces_blocked_stt_without_recording_the_stale_transcript():
    entered = threading.Event()
    release = threading.Event()

    class BlockingTranscriber(NullTranscriber):
        def final(self, pcm):
            entered.set()
            assert release.wait(5)
            return "stale spoken request"

    engine = ObserverEngine()
    session, emitted = make_session(
        engine, detector=SpeechDetector(), transcriber=BlockingTranscriber()
    )
    try:
        session.feed_audio(FRAME)
        session.feed_audio(FRAME)
        assert entered.wait(5)
        session.create_item({"kind": "message", "content": "new typed request"})
        session.create_response()
        release.set()
        wait_until(lambda: any(e["type"] == "response.done" for e in emitted))
        assert engine.calls == 1
        assert [message.content for message in session.conversation.history() if message.role == "user"] == [
            "new typed request"
        ]
    finally:
        session.close()


class ObserverEngine:
    def __init__(self, text="Done."):
        self.text = text
        self.calls = 0
        self.seen = []

    def tool_names(self):
        return []

    def respond_streaming(self, conversation, client_tools=None, observer=None):
        self.calls += 1
        self.seen.append([message.to_chat() for message in conversation.request_history()])
        if observer:
            observer("thinking")
        yield self.text


def test_playback_ack_defers_and_coalesces_attention_until_matching_drain():
    engine = ObserverEngine()
    session, emitted = make_session(engine)
    try:
        updated = session.update({"source_id": "browser-123", "playback_ack": True, "visual_context": True})
        assert updated["source_id"] == "browser-123" and updated["playback_ack"] is True
        session.create_item({"kind": "message", "content": "hello"})
        session.create_response()
        wait_until(lambda: len([e for e in emitted if e["type"] == "response.done"]) == 1)
        response_id = next(e["response"]["id"] for e in emitted if e["type"] == "response.created")
        for index in range(12):
            session.add_context(f"the scene changed {index} (browser-123)")
            assert session.wake() is False
        assert session.playback_update(response_id, False) is True
        wait_until(lambda: len([e for e in emitted if e["type"] == "response.done"]) == 2)
        assert engine.calls == 2
        context = engine.seen[1][-1]["content"]
        assert context.count("[perception]") == 8
        assert "changed 3 " not in context and "changed 4 " in context and "changed 11 " in context
    finally:
        session.close()


def test_buffered_legacy_context_does_not_become_an_unsolicited_retry_without_wake():
    entered = threading.Event()
    release = threading.Event()

    class BlockingEngine(ObserverEngine):
        def respond_streaming(self, conversation, client_tools=None, observer=None):
            self.calls += 1
            entered.set()
            assert release.wait(5)
            yield self.text

    engine = BlockingEngine()
    session, emitted = make_session(engine)
    try:
        session.create_item({"kind": "message", "content": "hello"})
        session.create_response()
        assert entered.wait(5)
        session.add_context("legacy scene update")
        release.set()
        wait_until(lambda: any(e["type"] == "response.done" for e in emitted))
        time.sleep(0.05)
        assert engine.calls == 1
        assert session._has_context() is True
    finally:
        session.close()


def test_stale_playback_ack_cannot_release_newer_response_attention():
    session, emitted = make_session(ObserverEngine())
    try:
        session.update({"playback_ack": True})
        for text in ("one", "two"):
            session.create_item({"kind": "message", "content": text})
            session.create_response()
            wait_until(lambda: len([e for e in emitted if e["type"] == "response.done"]) >= (1 if text == "one" else 2))
        ids = [e["response"]["id"] for e in emitted if e["type"] == "response.created"]
        session.add_context("someone entered")
        assert session.wake() is False
        assert session.playback_update(ids[0], False) is False
        time.sleep(0.05)
        assert len([e for e in emitted if e["type"] == "response.done"]) == 2
        assert session.playback_update(ids[1], False) is True
        wait_until(lambda: len([e for e in emitted if e["type"] == "response.done"]) == 3)
    finally:
        session.close()


class SpeechDetector(NullDetector):
    def __init__(self):
        self.in_speech = False
        self.calls = 0

    def feed(self, frame):
        self.calls += 1
        if self.calls == 1:
            self.in_speech = True
            return [("speech_started",)]
        self.in_speech = False
        return [("utterance", b"pcm")]


def test_wake_during_user_speech_waits_for_real_idle_boundary():
    detector = SpeechDetector()
    session, emitted = make_session(ObserverEngine(), detector=detector)
    try:
        session.feed_audio(FRAME)
        wait_until(lambda: detector.in_speech)
        session.add_context("someone entered")
        assert session.wake() is False
        session.feed_audio(FRAME)
        wait_until(lambda: any(e["type"] == "response.done" for e in emitted))
        assert any(e["response"]["unsolicited"] is True for e in emitted if e["type"] == "response.created")
    finally:
        session.close()


def test_legacy_client_becomes_idle_at_response_done_without_playback_ack():
    session, emitted = make_session(ObserverEngine())
    try:
        session.create_item({"kind": "message", "content": "hello"})
        session.create_response()
        wait_until(lambda: any(e["type"] == "response.done" for e in emitted))
        session.add_context("someone entered")
        assert session.wake() is True
    finally:
        session.close()


class RoutedSession:
    def __init__(self, source_id=None, visual_context=False):
        self.source_id = source_id
        self.visual_context = visual_context
        self.lines = []
        self.wakes = 0

    def add_context(self, line):
        self.lines.append(line)

    def wake(self):
        self.wakes += 1
        return True


def test_registry_routes_sources_and_only_wakes_visual_clients_for_scene_changes():
    registry = SessionRegistry()
    alpha = RoutedSession("browser-alpha", True)
    beta = RoutedSession("browser-beta", True)
    legacy = RoutedSession()
    for session in (alpha, beta, legacy):
        registry.add(session)

    assert registry.offer_context(
        "the scene changed (browser-alpha)", source_id="browser-alpha", kind="scene_changed"
    )
    assert alpha.lines == ["the scene changed (browser-alpha)"] and alpha.wakes == 1
    assert beta.lines == []
    assert legacy.lines == ["the scene changed (browser-alpha)"] and legacy.wakes == 0

    registry.offer_context(
        "someone entered (browser-beta)", wake=True, source_id="browser-beta", kind="person_entered"
    )
    assert alpha.lines == ["the scene changed (browser-alpha)"]
    assert beta.lines == ["someone entered (browser-beta)"] and beta.wakes == 1
    assert legacy.lines[-1] == "someone entered (browser-beta)" and legacy.wakes == 1


def test_source_ids_are_validated_consistently_in_session_and_frame_http(tmp_path):
    from richard.memory import MemoryStore
    from richard.satellite.relays import RelayRegistry
    from richard.web.app import WebApp

    valid = "browser-12345678-1234-1234-1234-123456789abc"
    session, _ = make_session(ObserverEngine())
    assert session.update({"source_id": valid})["source_id"] == valid
    with pytest.raises(ValueError, match="source_id"):
        session.update({"source_id": "https://camera.example/private?q=secret"})
    session.close()

    class Service:
        def __init__(self):
            self.seen = []

        def push_frame(self, source, jpeg):
            self.seen.append(source)

    service = Service()
    app = WebApp(
        config_path=tmp_path / "config.toml",
        memory_store=MemoryStore(":memory:"),
        relays=RelayRegistry(),
        perception=lambda: service,
    )
    body = {"source": valid, "image_base64": base64.b64encode(b"jpeg").decode()}
    response = app.handle("POST", "/api/perception/frame", __import__("json").dumps(body).encode())
    assert response.status == 200 and service.seen == [valid]
    body["source"] = "x" * 65
    response = app.handle("POST", "/api/perception/frame", __import__("json").dumps(body).encode())
    assert response.status == 400 and service.seen == [valid]


def test_factory_clones_camera_provider_and_binds_each_session_source():
    from richard.cli import _realtime_session_factory

    class Service:
        def __init__(self):
            self.calls = []

        def live_sources(self):
            return ["browser-alpha", "browser-beta"]

        def snapshot(self, source_id=None, detail="low", region=None):
            self.calls.append((source_id, detail))
            return b"jpeg", {"source": source_id, "width": 1, "height": 1, "age_s": 0.1}

    service = Service()
    shared = CameraProvider(service)
    config = Config()
    factory = _realtime_session_factory(
        config,
        brain=ScriptBrain([]),
        providers_fn=lambda: [shared],
        synth=RecordingTTS(),
        transcriber=NullTranscriber(),
        vad_factory=lambda: type("Vad", (), {"is_speech": lambda *a, **k: False, "reset": lambda self: None})(),
        perception=service,
    )
    a, b = factory(lambda event: None), factory(lambda event: None)
    try:
        a.update({"source_id": "browser-alpha", "visual_context": True})
        b.update({"source_id": "browser-beta", "visual_context": True})
        pa = a._engine._providers[0]
        pb = b._engine._providers[0]
        pa.execute("camera", {})
        pb.execute("camera", {})
        assert pa is not pb and pa is not shared and pb is not shared
        assert service.calls[-2:] == [("browser-alpha", "low"), ("browser-beta", "low")]
    finally:
        a.close()
        b.close()


def test_activity_event_shapes_and_playback_update_validation():
    assert events.response_created("resp_1", turn_id="turn_1", unsolicited=False) == {
        "type": "response.created",
        "response": {"id": "resp_1", "turn_id": "turn_1", "unsolicited": False},
    }
    assert events.response_activity("resp_1", "turn_1", "tool", unsolicited=True) == {
        "type": "response.activity",
        "response_id": "resp_1",
        "turn_id": "turn_1",
        "phase": "tool",
        "unsolicited": True,
    }
    parsed = events.parse_client_event(
        '{"type":"playback.update","response_id":"resp_1","playing":false}'
    )
    assert parsed["response_id"] == "resp_1" and parsed["playing"] is False
    with pytest.raises(ValueError, match="playing"):
        events.parse_client_event('{"type":"playback.update","response_id":"resp_1","playing":0}')
