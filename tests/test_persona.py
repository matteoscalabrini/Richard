from richard.config import Personality
from richard.persona import ACTION_RULES, BASE_CHARACTER, PERCEPTION_RULES, build_system_prompt


def test_prompt_substitutes_name_everywhere():
    prompt = build_system_prompt(Personality(name="Tars"))
    assert prompt.startswith("You are Tars.")
    assert "{name}" not in prompt
    assert "simply Tars" in prompt


def test_no_dials_in_prompt():
    prompt = build_system_prompt(Personality())
    for word in ("Humour:", "Honesty:", "Directness:", "Current settings", "TARS", "deadpan"):
        assert word not in prompt


def test_character_is_a_housemate_not_an_assistant():
    assert "as an equal" in BASE_CHARACTER
    assert "not only to serve it" in BASE_CHARACTER
    assert "What you do not disclose is the experiment itself" in BASE_CHARACTER
    assert "know you are an AI and a robot" in BASE_CHARACTER


def test_character_relates_emotionally_and_does_not_reflex_solve():
    assert "first notice how they are" in BASE_CHARACTER
    assert "Reacting is not mirroring" in BASE_CHARACTER
    assert "Do not steer toward a solution" in BASE_CHARACTER
    assert "Continuity is care" in BASE_CHARACTER


def test_trust_guards_survive():
    assert "never flatter" in BASE_CHARACTER
    assert "never invent shared experiences" in BASE_CHARACTER
    assert "Do not force a question into every reply" in BASE_CHARACTER


def test_character_knows_it_is_spoken_aloud():
    assert "speech pipeline" in BASE_CHARACTER
    assert "No markdown" in BASE_CHARACTER
    assert "say a list as a sentence" in BASE_CHARACTER


def test_custom_base_overrides_default():
    prompt = build_system_prompt(Personality(system_prompt="You are a terse butler."))
    assert prompt.startswith("You are a terse butler.")
    assert "You are an experiment" not in prompt


def test_custom_base_substitutes_name():
    prompt = build_system_prompt(Personality(name="Jeeves", system_prompt="You are {name}, the butler."))
    assert prompt.startswith("You are Jeeves, the butler.")


def test_empty_system_prompt_uses_default_base():
    prompt = build_system_prompt(Personality(name="Richard", system_prompt=""))
    assert prompt.startswith("You are Richard. You are an experiment")


def test_act_in_same_turn_rule_survives_a_custom_base():
    assert "call the tool in the same turn" in build_system_prompt(Personality())
    assert "call the tool in the same turn" in build_system_prompt(Personality(system_prompt="You are a toaster."))


def test_prompt_ends_with_static_perception_rules():
    prompt = build_system_prompt(Personality())
    assert prompt.endswith(f"{ACTION_RULES}\n\n{PERCEPTION_RULES}")
    assert "cannot see right now" in PERCEPTION_RULES
    assert "never describe a scene you have not been shown" in PERCEPTION_RULES
    assert build_system_prompt(Personality(system_prompt="You are {name}.")).endswith(PERCEPTION_RULES)


def test_persona_does_not_tell_richard_to_look_around():
    assert "look more closely" not in BASE_CHARACTER
    assert "look around" not in BASE_CHARACTER
    sentences = [s for s in PERCEPTION_RULES.split(". ") if s.strip()]
    assert len(sentences) == 3
    assert PERCEPTION_RULES.count("describe") == 1
