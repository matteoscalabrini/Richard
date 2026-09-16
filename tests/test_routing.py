import pytest

from richard.routing import role_for


@pytest.mark.parametrize("text", [
    "who wrote Merrily We Roll Along and when did it open",
    "why is the sky red at sunset here in Italy",
    "spiegami come funziona una pompa di calore",
    "quale musical ha scritto Sondheim nel 1970",
])
def test_knowledge_questions_go_to_thinking(text):
    assert role_for(text) == "thinking"


@pytest.mark.parametrize("text", [
    "what time is it",
    "turn the kitchen light off",
    "che ore sono",
    "what am I holding",
    "how are you",
    "quando hai visto Anna l'ultima volta",
    "look at this and tell me what it is",
    "can you look at this and explain what it does",
    "what do you see on my desk",
    "guarda qui e dimmi cosa vedi",
    None, "",
])
def test_everything_else_stays_conversational(text):
    assert role_for(text) == "conversational"


@pytest.mark.parametrize("text", [
    "come stai oggi, tutto bene?",
    "how was your day today then",
    "what do you think about that idea",
    "cosa ne pensi di questo piano",
    "who is in the room right now",
    "what colour is the mug I am holding",
    "how many people are in the frame",
    "how do I turn on the kitchen lights",
    "what did I tell you yesterday about tea",
    "[AUTOMATED CHECK] is anything on fire",
    "[SCHEDULED] time to remind matteo about the meeting",
])
def test_ordinary_chat_vision_and_automated_prompts_stay_conversational(text):
    assert role_for(text) == "conversational"
