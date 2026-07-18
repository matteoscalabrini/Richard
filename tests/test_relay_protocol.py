import pytest

from richard.satellite.protocol import Hello, SessionStart, State, encode, decode


def test_roundtrip_hello():
    msg = Hello(relay_id="E4B32305E50C", room_id="r1", room_name="Kitchen",
                capabilities=["voice"])
    assert decode(encode(msg)) == msg


def test_roundtrip_control_messages():
    for msg in [
        SessionStart(),
        State(state="listening"),
    ]:
        assert decode(encode(msg)) == msg


def test_encode_is_json_text_with_type_tag():
    import json
    data = json.loads(encode(State(state="idle")))
    assert data["type"] == "state"
    assert data["state"] == "idle"


def test_decode_rejects_unknown_type():
    with pytest.raises(ValueError, match="unknown relay message type"):
        decode('{"type": "nope"}')
