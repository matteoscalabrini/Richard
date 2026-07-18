import asyncio
import base64
import json

from richard.realtime.server import handle_realtime


class FakeRequest:
    def __init__(self, path="/v1/realtime", headers=None):
        self.path = path
        self.headers = headers or {}


class FakeWs:
    def __init__(self, inbound, path="/v1/realtime", headers=None):
        self.inbound = list(inbound)
        self.sent = []
        self.closed = None
        self.request = FakeRequest(path, headers)

    def __aiter__(self):
        return self

    async def __anext__(self):
        await asyncio.sleep(0)
        if not self.inbound:
            raise StopAsyncIteration
        return self.inbound.pop(0)

    async def send(self, data):
        self.sent.append(json.loads(data))

    async def close(self, code=1000, reason=""):
        self.closed = (code, reason)


class FakeSession:
    def __init__(self, emit):
        self.emit = emit
        self.audio = b""
        self.texts = []
        self.cancelled = False
        self.closed = False
        self.samplerate = 24000

    def feed_audio(self, pcm):
        self.audio += pcm

    def create_text_item(self, text):
        self.texts.append(text)

    def cancel_response(self):
        self.cancelled = True

    def update(self, patch):
        return {"barge_in": patch.get("barge_in", "vad")}

    def close(self):
        self.closed = True


def run(ws, token=""):
    made = []

    def factory(emit):
        s = FakeSession(emit)
        made.append(s)
        return s

    asyncio.run(asyncio.wait_for(handle_realtime(ws, factory, token=token), timeout=5))
    return made


def _append(pcm=b"\x01\x02"):
    return json.dumps({"type": "input_audio_buffer.append",
                       "audio": base64.b64encode(pcm).decode()})


def test_session_created_announced_first():
    ws = FakeWs([])
    run(ws)
    assert ws.sent[0]["type"] == "session.created"
    assert ws.sent[0]["session"]["output_audio_samplerate"] == 24000


def test_audio_and_text_and_cancel_are_dispatched():
    ws = FakeWs([
        _append(b"\xaa\xbb"),
        json.dumps({"type": "conversation.item.create",
                    "item": {"content": [{"type": "input_text", "text": "hi"}]}}),
        json.dumps({"type": "response.cancel"}),
    ])
    (session,) = run(ws)
    assert session.audio == b"\xaa\xbb"
    assert session.texts == ["hi"]
    assert session.cancelled is True
    assert session.closed is True  # socket end closes the session


def test_bad_event_gets_error_and_connection_survives():
    ws = FakeWs(["not json", _append(b"\x01")])
    (session,) = run(ws)
    errors = [m for m in ws.sent if m["type"] == "error"]
    assert len(errors) == 1
    assert session.audio == b"\x01"  # kept serving after the bad event


def test_token_required_when_configured():
    ws = FakeWs([], path="/v1/realtime")
    made = run(ws, token="sekrit")
    assert made == []  # no session
    assert ws.closed[0] == 4001


def test_token_accepted_via_query_and_bearer():
    ok_query = FakeWs([], path="/v1/realtime?token=sekrit")
    assert run(ok_query, token="sekrit")  # session was created
    ok_header = FakeWs([], headers={"Authorization": "Bearer sekrit"})
    assert run(ok_header, token="sekrit")


def test_session_update_echoes_effective_settings():
    ws = FakeWs([json.dumps({"type": "session.update", "session": {"barge_in": "off"}})])
    run(ws)
    updated = [m for m in ws.sent if m["type"] == "session.updated"]
    assert updated and updated[0]["session"]["barge_in"] == "off"


def test_non_dict_session_update_gets_error_and_survives():
    ws = FakeWs([
        json.dumps({"type": "session.update", "session": "boom"}),
        _append(b"\x01"),
    ])
    (session,) = run(ws)
    assert any(m["type"] == "error" for m in ws.sent)
    assert session.audio == b"\x01"


def test_non_dict_item_create_gets_error_and_survives():
    ws = FakeWs([
        json.dumps({"type": "conversation.item.create", "item": "boom"}),
        _append(b"\x02"),
    ])
    (session,) = run(ws)
    assert any(m["type"] == "error" for m in ws.sent)
    assert session.texts == []
    assert session.audio == b"\x02"


def test_wrong_token_rejected_via_query_and_bearer():
    bad_query = FakeWs([], path="/v1/realtime?token=nope")
    assert run(bad_query, token="sekrit") == []
    assert bad_query.closed[0] == 4001
    bad_header = FakeWs([], headers={"Authorization": "Bearer nope"})
    assert run(bad_header, token="sekrit") == []
    assert bad_header.closed[0] == 4001
