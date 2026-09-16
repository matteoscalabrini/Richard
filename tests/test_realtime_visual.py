import pytest

from richard.realtime.visual import wants_frame


@pytest.mark.parametrize("text", [
    "what am I holding",
    "Look at this.",
    "can you see the cup?",
    "describe the room",
    "what colour is my shirt",
    "guarda qui",
    "cosa vedi?",
    "dai un'occhiata alla scrivania",
    "che cosa ho in mano",
    "com'è la mia maglietta oggi",
    "fai una foto",
    "take a picture",
])
def test_visual_requests_want_a_frame(text):
    assert wants_frame(text, unsolicited=False, context=None)


@pytest.mark.parametrize("text", [
    "what time is it",
    "turn the kitchen light off",
    "che ore sono",
    "raccontami una barzelletta",
    "I see what you mean, let's move on",   # idiom, not a request to look
    "see you later",
])
def test_ordinary_turns_do_not_want_a_frame(text):
    assert not wants_frame(text, unsolicited=False, context=None)


def test_unsolicited_scene_change_wants_a_frame_but_arrivals_do_not():
    assert wants_frame(None, unsolicited=True, context="[perception] 10:02 the scene changed (browser)")
    assert wants_frame(None, unsolicited=True, context="[perception] 10:02 the camera view changed (browser)")
    assert not wants_frame(None, unsolicited=True, context="[perception] 10:02 matteo entered (browser)")
    assert not wants_frame(None, unsolicited=False, context=None)
