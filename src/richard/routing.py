from __future__ import annotations

import re

# A "knowledge question" (trivia, explanations, general facts) is routed to the
# thinking brain role when one is configured. It is recognised, not understood: a
# wh-word (EN/IT) present, no household/vision/tool word present (those turns need
# the fast conversational brain with tool access, not a slow "thinking" model), and
# at least five words (short questions like "what time is it" are device queries,
# not knowledge questions, and are cheaply filtered by length alone).
# "how"/"come" are handled separately (see _HOW_RE below): bare, they are as likely to
# start ordinary chat ("how are you", "how was your day") as a knowledge question.
_WH_RE = re.compile(
    r"(?i)\b(?:who|what|when|where|why|which|explain|"
    r"chi|cosa|quando|dove|perch[eé]|quale|spiegami|dimmi)\b"
    r"|che\s+cos|tell me about"
)

# "how"/"come" count as a knowledge trigger only when immediately followed by a word
# that makes them a genuine "how does X work" question ("how many people are in the
# frame" still needs the household/vision exclusion below to stay conversational; "how
# are you"/"come stai" never matches here at all).
_HOW_RE = re.compile(
    r"(?i)\bhow\s+(?:does|do|did|is|are|many|much|long|far)\b"
    r"|\bcome\s+(?:funziona|funzionano|si|mai)\b"
)

# Household/vision/tool words, ordinary chat about feelings/opinions/the day, and
# room/vision-scoped questions: their presence means the turn is a device request, a
# personal check-in, or a question about *this* room, not trivia, even if it happens to
# contain a wh-word ("look at this and tell me what it is" is a camera request; "quando
# hai visto Anna l'ultima volta" is personal, not trivia; "who is in the room right now"
# is a vision question, not a knowledge one). `look`/`see` and their common inflections
# are included: a bare "look" or "see" is what a camera request actually says ("what do
# you see", "look at this").
_HOUSEHOLD_RE = re.compile(
    r"(?i)\b(?:light|lights|lamp|lamps|luce|luci|lampada|lampade|faretti|switch|accendi|spegni|"
    r"temperature|temperatura|camera|look(?:s|ing)?|see(?:s|ing)?|seen|"
    r"guarda|vedi|visto|foto|remember|ricorda|"
    r"forget|time|ore|weather|meteo|search|cerca|"
    r"holding|frame|room|stanza|mug|desk|scrivania|think|pensi|pensa|"
    r"tell you|told you|detto|hai detto|yesterday|ieri|today|oggi|day|giornata|stai|sta|feel)\b"
)

_MIN_WORDS = 5


def role_for(user_text: str | None) -> str:
    """"thinking" for knowledge questions when one is configured, else "conversational"."""
    if not user_text:
        return "conversational"
    # A bracketed line is a system-authored prompt (an automated check-in, a scheduled
    # nudge), never the user's own knowledge question.
    if user_text.lstrip().startswith("["):
        return "conversational"
    if len(user_text.split()) < _MIN_WORDS:
        return "conversational"
    if not (_WH_RE.search(user_text) or _HOW_RE.search(user_text)):
        return "conversational"
    if _HOUSEHOLD_RE.search(user_text):
        return "conversational"
    return "thinking"
