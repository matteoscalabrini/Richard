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
    ('{"type": "response.nope"}', "unknown"),
    ('{"type": "input_audio_buffer.append", "audio": "@@@"}', "base64"),
])
def test_parse_rejects_bad_events(raw, fragment):
    with pytest.raises(ValueError, match=fragment):
        events.parse_client_event(raw)


def test_new_ids_are_unique_and_prefixed():
    a, b = events.new_id("resp"), events.new_id("resp")
    assert a != b and a.startswith("resp_") and b.startswith("resp_")


IMG = "data:image/jpeg;base64,/9j/4AAQ"


def test_response_create_is_a_known_client_event():
    assert events.parse_client_event('{"type": "response.create"}')["type"] == "response.create"


def test_parse_item_text_only_message_is_a_string():
    item = {"type": "message", "role": "user", "content": [{"type": "input_text", "text": " hi "}]}
    assert events.parse_item(item) == {"kind": "message", "content": "hi"}


def test_parse_item_without_type_is_a_message():
    assert events.parse_item({"content": [{"type": "input_text", "text": "hi"}]}) == {"kind": "message", "content": "hi"}


def test_parse_item_image_message_becomes_parts():
    item = {"type": "message", "role": "user", "content": [
        {"type": "input_text", "text": "what"}, {"type": "input_image", "image_url": IMG}]}
    assert events.parse_item(item) == {"kind": "message", "content": [
        {"type": "text", "text": "what"}, {"type": "image_url", "image_url": {"url": IMG}}]}
    only_image = {"type": "message", "role": "user", "content": [{"type": "input_image", "image_url": IMG}]}
    assert events.parse_item(only_image)["content"] == [{"type": "image_url", "image_url": {"url": IMG}}]


def test_parse_item_function_call_output():
    item = {"type": "function_call_output", "call_id": "c1", "output": '{"image_attached": true}'}
    assert events.parse_item(item) == {"kind": "function_call_output", "call_id": "c1", "output": '{"image_attached": true}'}


@pytest.mark.parametrize("item,fragment", [
    ({"type": "message", "content": "not a list"}, "content"),
    ({"type": "message", "content": []}, "no usable content"),
    ({"type": "message", "content": [{"type": "input_image", "image_url": "http://x/y.jpg"}]}, "data:image"),
    ({"type": "message", "role": "assistant", "content": [{"type": "input_text", "text": "x"}]}, "role"),
    ({"type": "function_call_output", "output": "x"}, "call_id"),
    ({"type": "function_call_output", "call_id": "c1", "output": 5}, "output"),
    ({"type": "function_call"}, "unsupported"),
])
def test_parse_item_rejects(item, fragment):
    with pytest.raises(ValueError, match=fragment):
        events.parse_item(item)


def test_tools_to_schemas_converts_flat_specs_and_drops_reserved():
    tools = [
        {"type": "function", "name": "camera", "description": "look", "parameters": {"type": "object", "properties": {}}},
        {"type": "function", "name": "remember", "description": "x", "parameters": {}},
        {"type": "function", "name": "", "parameters": {}},
        "junk",
    ]
    schemas, dropped = events.tools_to_schemas(tools, reserved={"remember"})
    assert schemas == [{"type": "function", "function": {
        "name": "camera", "description": "look", "parameters": {"type": "object", "properties": {}}}}]
    assert dropped == ["remember"]


def test_tools_to_schemas_defaults_missing_parameters():
    schemas, _ = events.tools_to_schemas([{"name": "go_to_sleep"}])
    assert schemas[0]["function"]["parameters"] == {"type": "object", "properties": {}}
    assert schemas[0]["function"]["description"] == ""


def test_function_call_arguments_done_shape():
    e = events.function_call_arguments_done("resp_1", "call_1", "camera", '{"question": "q"}')
    assert e["type"] == "response.function_call_arguments.done"
    assert e["response_id"] == "resp_1" and e["call_id"] == "call_1"
    assert e["name"] == "camera" and e["arguments"] == '{"question": "q"}'
    assert e["item_id"].startswith("item_") and e["event_id"].startswith("event_")
    assert e["output_index"] == 0


def test_active_response_code():
    assert events.ACTIVE_RESPONSE_CODE == "conversation_already_has_active_response"
