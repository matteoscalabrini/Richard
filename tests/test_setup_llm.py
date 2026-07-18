from richard.setup.llm import LlmChoice, configure_llm, DEFAULT_LLM_ENDPOINT, DEFAULT_LLM_MODEL


def _must_not_call(*args, **kwargs):  # prompt must never run on the flag/non-interactive paths
    raise AssertionError("prompt should not be called")


def test_flags_take_precedence_non_interactive():
    choice = configure_llm(
        url="http://10.0.0.5:8080", key="sk-abc", model="Gemma4",
        interactive=False, prompt=_must_not_call,
    )
    assert choice == LlmChoice("http://10.0.0.5:8080", "sk-abc", "Gemma4")


def test_non_interactive_without_flags_uses_defaults():
    choice = configure_llm(interactive=False, prompt=_must_not_call)
    assert choice.endpoint == DEFAULT_LLM_ENDPOINT
    assert choice.model == DEFAULT_LLM_MODEL
    assert choice.api_key is None


def test_interactive_prompts_with_defaults_when_blank():
    # User just presses Enter at every prompt -> defaults.
    answers = iter(["", "", ""])
    choice = configure_llm(interactive=True, prompt=lambda label, default="": next(answers) or default)
    assert choice.endpoint == DEFAULT_LLM_ENDPOINT
    assert choice.model == DEFAULT_LLM_MODEL
    assert choice.api_key is None


def test_interactive_accepts_typed_values():
    answers = iter(["http://host:1234", "my-model", "sk-xyz"])
    choice = configure_llm(interactive=True, prompt=lambda label, default="": next(answers) or default)
    assert choice == LlmChoice("http://host:1234", "sk-xyz", "my-model")


def test_blank_api_key_becomes_none():
    answers = iter(["http://host:1234", "m", ""])
    choice = configure_llm(interactive=True, prompt=lambda label, default="": next(answers) or default)
    assert choice.api_key is None
