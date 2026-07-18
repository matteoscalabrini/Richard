from __future__ import annotations

from typing import Protocol


class Provider(Protocol):
    def schemas(self) -> list[dict]:
        """OpenAI function schemas for this provider's tools."""
        ...

    def execute(self, name: str, arguments: dict) -> str:
        """Run one of this provider's tools, returning a short result string."""
        ...

    def context(self) -> str | None:
        """A system-prompt section for this provider, or None."""
        ...
