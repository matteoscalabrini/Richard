"""Web search as a Richard plugin, backed by Tavily."""
from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

from richard.plugins.base import PluginContext, PluginParts
from richard.plugins.web_search.provider import WebSearchProvider
from richard.plugins.web_search.tavily import TavilyClient


class WebSearchPlugin:
    name = "web_search"
    version = "1.0"

    def __init__(self, client_factory: Callable[..., TavilyClient] = TavilyClient) -> None:
        self._client_factory = client_factory

    def config_defaults(self) -> dict:
        return {"api_key_file": "~/.richard/tavily.key", "max_results": 3}

    def build(self, ctx: PluginContext) -> PluginParts:
        path = Path(str(ctx.config.get("api_key_file", ""))).expanduser()
        api_key = path.read_text().strip() if path.is_file() else ""
        if not api_key:
            raise ValueError(f"web_search: api key file {path} is missing or empty")
        try:
            max_results = int(ctx.config.get("max_results", 3))
        except (TypeError, ValueError):
            max_results = 3
        max_results = max(1, min(10, max_results))
        client = self._client_factory(api_key)
        return PluginParts(providers=[WebSearchProvider(client, max_results=max_results)])
