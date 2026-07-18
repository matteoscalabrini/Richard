from richard.config import Personality
from richard.persona import build_system_prompt


def test_prompt_includes_name():
    prompt = build_system_prompt(Personality(name="Tars"))
    assert prompt.startswith("You are Tars,")


def test_default_prompt_matches_default_bands():
    prompt = build_system_prompt(Personality())  # humour 70, honesty 90, directness 60
    assert "Humour: 70% — land a dry, deadpan beat fairly often; never slapstick." in prompt
    assert "Honesty: 90% — candid and direct; minimal softening, never deceive or flatter." in prompt
    assert "Directness: 60% — get to the point with minimal preamble; a brief aside is fine." in prompt


def test_humour_bands():
    assert "essentially none; play it straight" in build_system_prompt(Personality(humour=10))
    assert "occasional light dryness" in build_system_prompt(Personality(humour=35))
    assert "land a dry, deadpan beat fairly often" in build_system_prompt(Personality(humour=70))
    assert "frequent dry wit" in build_system_prompt(Personality(humour=95))


def test_honesty_bands():
    assert "diplomatic; soften hard truths" in build_system_prompt(Personality(honesty=20))
    assert "honest but tactful" in build_system_prompt(Personality(honesty=60))
    assert "candid and direct" in build_system_prompt(Personality(honesty=90))
    assert "blunt; unvarnished truth" in build_system_prompt(Personality(honesty=100))


def test_directness_bands():
    assert "conversational; a little preamble is fine" in build_system_prompt(Personality(directness=10))
    assert "get to the point with minimal preamble" in build_system_prompt(Personality(directness=60))
    assert "terse; answer first" in build_system_prompt(Personality(directness=90))


def test_prompt_invites_stating_settings():
    prompt = build_system_prompt(Personality())
    assert "state them if asked" in prompt




def test_custom_base_overrides_default():
    # A custom system prompt replaces the TARS base paragraph...
    prompt = build_system_prompt(Personality(system_prompt="You are a terse butler."))
    assert prompt.startswith("You are a terse butler.")
    assert "companion and steward" not in prompt  # default base is gone
    # ...but the dial settings block is still appended
    assert "Humour:" in prompt and "Honesty:" in prompt and "Directness:" in prompt


def test_custom_base_substitutes_name():
    prompt = build_system_prompt(Personality(name="Jeeves", system_prompt="You are {name}, the butler."))
    assert prompt.startswith("You are Jeeves, the butler.")


def test_empty_system_prompt_uses_default_base():
    prompt = build_system_prompt(Personality(name="Richard", system_prompt=""))
    assert prompt.startswith("You are Richard, companion and steward")


def test_act_in_same_turn_rule_survives_a_custom_base():
    # The rule is an operating contract, not a personality trait: it must hold
    # even when the user replaces the base character prompt entirely.
    assert "call the tool in the same turn" in build_system_prompt(Personality())
    custom = build_system_prompt(Personality(system_prompt="You are a toaster."))
    assert "call the tool in the same turn" in custom
