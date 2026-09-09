from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True)
class ToolResult:
    """A tool result that carries pictures. The engine serves `text` as the tool message
    and the images as a user message right after it (the Reachy app's own sequence)."""

    text: str
    images: tuple[str, ...] = ()  # data URLs


class Provider(Protocol):
    def schemas(self) -> list[dict]:
        """OpenAI function schemas for this provider's tools."""
        ...

    def execute(self, name: str, arguments: dict) -> "str | ToolResult":
        """Run one of this provider's tools, returning a short result string (or a
        ToolResult when the result includes pictures)."""
        ...

    def context(self) -> str | None:
        """A system-prompt section for this provider, or None."""
        ...
