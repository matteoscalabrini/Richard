from __future__ import annotations

import re

# A "knowledge question" (trivia, explanations, general facts) is routed to the
# thinking brain role when one is configured. It is recognised, not understood: a
# wh-word (EN/IT) present, no household/vision/tool word present (those turns need
# the fast conversational brain with tool access, not a slow "thinking" model), and
# at least five words (short questions like "what time is it" are device queries,
# not knowledge questions, and are cheaply filtered by length alone).
_WH_RE = re.compile(
    r"(?i)\b(?:who|what|when|where|why|how|which|explain|"
    r"chi|cosa|quando|dove|perch[eé]|come|quale|spiegami|dimmi)\b"
    r"|che\s+cos|tell me about"
)

# Household/vision/tool words: their presence means the turn is a device or
# perception request, not a knowledge question, even if it happens to contain a
# wh-word ("why does the sky look red" is knowledge; "quando hai visto Anna
# l'ultima volta" is a personal/vision question about *this* room, not trivia).
_HOUSEHOLD_RE = re.compile(
    r"(?i)\b(?:light|lamp|luce|lampada|lampade|faretti|switch|accendi|spegni|"
    r"temperature|temperatura|camera|guarda|vedi|visto|foto|remember|ricorda|"
    r"forget|time|ore|weather|meteo|search|cerca)\b"
)

_MIN_WORDS = 5


def role_for(user_text: str | None) -> str:
    """"thinking" for knowledge questions when one is configured, else "conversational"."""
    if not user_text:
        return "conversational"
    if len(user_text.split()) < _MIN_WORDS:
        return "conversational"
    if not _WH_RE.search(user_text):
        return "conversational"
    if _HOUSEHOLD_RE.search(user_text):
        return "conversational"
    return "thinking"
