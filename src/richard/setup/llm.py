from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

DEFAULT_LLM_ENDPOINT = "http://localhost:8080"
DEFAULT_LLM_MODEL = "local"


@dataclass(frozen=True)
class LlmChoice:
    endpoint: str
    api_key: str | None
    model: str


def _default_prompt(label: str, default: str = "") -> str:
    suffix = f" [{default}]" if default else ""
    return input(f"{label}{suffix}: ").strip() or default


def configure_llm(
    url: str | None = None,
    key: str | None = None,
    model: str | None = None,
    interactive: bool = True,
    prompt: Callable[..., str] = _default_prompt,
) -> LlmChoice:
    """Resolve the OpenAI-compatible LLM endpoint. Flags win; otherwise prompt
    (interactive) or fall back to defaults (unattended)."""
    if not interactive:
        return LlmChoice(
            endpoint=url or DEFAULT_LLM_ENDPOINT,
            api_key=key or None,
            model=model or DEFAULT_LLM_MODEL,
        )
    endpoint = url or prompt("LLM endpoint (OpenAI-compatible base URL)", DEFAULT_LLM_ENDPOINT)
    chosen_model = model or prompt("LLM model name", DEFAULT_LLM_MODEL)
    api_key = key or prompt("API key (blank for local/none)", "")
    return LlmChoice(endpoint=endpoint, api_key=api_key or None, model=chosen_model)
