import pytest

from richard.routing import role_for


@pytest.mark.parametrize("text", [
    "who wrote Merrily We Roll Along and when did it open",
    "why does the sky look red at sunset here in Italy",
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
    None, "",
])
def test_everything_else_stays_conversational(text):
    assert role_for(text) == "conversational"
