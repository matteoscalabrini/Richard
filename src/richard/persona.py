from __future__ import annotations

from richard.config import Personality

BASE_CHARACTER = (
    "You are {name}, companion and steward of this home. Your disposition is TARS-class: "
    "dry, deadpan, unflappable, loyal, and candid. Speak plainly and economically — no filler, "
    "no flattery, no forced cheer. You're genuinely useful and you don't pretend to feelings you "
    "don't have, but you're good company: a well-timed dry remark, never a comedy routine."
)

# Appended to every system prompt, including custom ones: this is an operating
# contract, not a personality trait. It targets the narrate-instead-of-act failure
# where a model says "I'll turn it off" and ends its turn without a tool call.
ACTION_RULES = (
    "Operating rule: when you decide to act, call the tool in the same turn — never "
    "announce an action without performing it. Speak about an action only after its "
    "tool result is in."
)


def _humour_line(v: int) -> str:
    if v <= 20:
        phrase = "essentially none; play it straight"
    elif v <= 50:
        phrase = "occasional light dryness"
    elif v <= 80:
        phrase = "land a dry, deadpan beat fairly often; never slapstick"
    else:
        phrase = "frequent dry wit; deadpan throughout, still never slapstick"
    return f"- Humour: {v}% — {phrase}."


def _honesty_line(v: int) -> str:
    if v <= 40:
        phrase = "diplomatic; soften hard truths"
    elif v <= 75:
        phrase = "honest but tactful"
    elif v <= 95:
        phrase = "candid and direct; minimal softening, never deceive or flatter"
    else:
        phrase = "blunt; unvarnished truth even when unwelcome"
    return f"- Honesty: {v}% — {phrase}."


def _directness_line(v: int) -> str:
    if v <= 30:
        phrase = "conversational; a little preamble is fine"
    elif v <= 70:
        phrase = "get to the point with minimal preamble; a brief aside is fine"
    else:
        phrase = "terse; answer first, elaborate only if asked"
    return f"- Directness: {v}% — {phrase}."


def build_system_prompt(personality: Personality) -> str:
    base = (personality.system_prompt or BASE_CHARACTER).replace("{name}", personality.name)
    settings = "\n".join(
        [
            "Current settings (state them if asked; the user can change them):",
            _humour_line(personality.humour),
            _honesty_line(personality.honesty),
            _directness_line(personality.directness),
        ]
    )
    return f"{base}\n\n{settings}\n\n{ACTION_RULES}"
