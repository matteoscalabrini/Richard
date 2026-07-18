import base64

import pytest

from richard.realtime import events


def test_audio_delta_base64_encodes_pcm():
    e = events.audio_delta("resp_1", b"\x01\x02\x03")
    assert e["type"] == "response.audio.delta"
    assert e["response_id"] == "resp_1"
    assert base64.b64decode(e["delta"]) == b"\x01\x02\x03"


def test_session_created_announces_samplerates():
    e = events.session_created("sess_1", output_samplerate=24000)
    assert e["session"]["id"] == "sess_1"
    assert e["session"]["output_audio_samplerate"] == 24000
    assert e["session"]["input_audio_samplerate"] == 16000


def test_parse_append_decodes_audio():
    raw = '{"type": "input_audio_buffer.append", "audio": "AQID"}'
    event = events.parse_client_event(raw)
    assert event["audio"] == b"\x01\x02\x03"


@pytest.mark.parametrize("raw,fragment", [
    ("not json", "JSON"),
    ('{"no": "type"}', "type"),
    ('{"type": "response.create"}', "unknown"),
    ('{"type": "input_audio_buffer.append", "audio": "@@@"}', "base64"),
])
def test_parse_rejects_bad_events(raw, fragment):
    with pytest.raises(ValueError, match=fragment):
        events.parse_client_event(raw)


def test_new_ids_are_unique_and_prefixed():
    a, b = events.new_id("resp"), events.new_id("resp")
    assert a != b and a.startswith("resp_") and b.startswith("resp_")
