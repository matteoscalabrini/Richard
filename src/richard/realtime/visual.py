"""Decide whether a turn is visual, i.e. whether the ambient camera frame is attached.

Before 2026-09-16 every turn of a client with video carried a fresh frame plus an
instruction to use it, which pulled the model toward describing scenes and made the
client speak the "let me take a look" cue on ordinary turns. A frame now rides only
when the user's words ask for eyes, or when Richard was woken by a scene change.
Camera tool calls are unaffected: the model can still decide to look on any turn.
"""
from __future__ import annotations

import re

# Verbs and nouns that ask for eyes, English and Italian. Word-bounded and
# case-insensitive. "see" alone is excluded: "I see", "see you" are not requests.
_VISUAL_RE = re.compile(
    r"(?i)(?:"
    r"\blook(?:s|ed|ing)?\b(?! forward)|"
    r"\bwatch(?:ing)?\b|"
    r"\bcan you see\b|\bdo you see\b|\bwhat do you see\b|\bsee (?:the|this|that|my)\b|"
    r"\bdescribe\b|\bpicture\b|\bphoto\b|\bcamera\b|\bwebcam\b|"
    r"\bholding\b|\bwearing\b|\bcolou?r\b|\bshow you\b|"
    r"\bguard(?:a|i|are|ate)\b|\bved(?:i|ere|ete)\b|\bcosa vedi\b|\bocchiata\b|"
    r"\bfoto\b|\bdescriv(?:i|ere|imi)\b|\bin mano\b|\bindoss(?:o|i|a)\b|"
    r"\bmaglietta\b|\bcamicia\b|\bcolore\b|\bmostr(?:o|a|arti)\b"
    r")"
)

_SCENE_CHANGE_RE = re.compile(r"\bthe scene changed\b")


def wants_frame(user_text: str | None, *, unsolicited: bool, context: str | None) -> bool:
    if user_text and _VISUAL_RE.search(user_text):
        return True
    if unsolicited and context and _SCENE_CHANGE_RE.search(context):
        return True
    return False
