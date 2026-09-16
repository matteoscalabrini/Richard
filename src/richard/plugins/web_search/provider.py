from __future__ import annotations

from richard.errors import WebSearchError
from richard.plugins.web_search.tavily import TavilyClient

_SNIPPET_CHARS = 200
_MAX_SOURCES = 3

WEB_SEARCH_SCHEMA = {
    "type": "function",
    "function": {
        "name": "web_search",
        "description": "Search the web for facts you do not know or that may have changed.",
        "parameters": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "The search query.",
                },
                "recent": {
                    "type": "boolean",
                    "description": "true for news or anything that changed in the last days",
                },
            },
            "required": ["query"],
        },
    },
}


class WebSearchProvider:
    def __init__(self, client: TavilyClient) -> None:
        self._client = client

    def schemas(self) -> list[dict]:
        return [WEB_SEARCH_SCHEMA]

    def context(self) -> str | None:
        return (
            "Web search is available with the web_search tool for facts you do not know "
            "or that may have changed; report what the tool returned and name the source, "
            "never assert beyond it."
        )

    def execute(self, name: str, arguments: dict) -> str:
        if name != "web_search":
            return f"Unknown tool: {name}."
        query = str(arguments.get("query", "")).strip()
        if not query:
            return "A search query is required."
        recent = bool(arguments.get("recent", False))
        try:
            result = self._client.search(query, recent=recent)
        except WebSearchError as exc:
            return str(exc)
        return self._format(result)

    def _format(self, result) -> str:
        has_answer = bool(result.answer)
        hits = result.results[:_MAX_SOURCES]
        if has_answer:
            sources = "; ".join(
                f"{i}. {hit.title} — {hit.url}" for i, hit in enumerate(hits, 1)
            )
            return f"{result.answer}\nSources: {sources}" if sources else result.answer
        if not hits:
            return "No web search results."
        sources = "; ".join(
            f"{i}. {hit.title} — {hit.url}: {hit.content[:_SNIPPET_CHARS]}"
            for i, hit in enumerate(hits, 1)
        )
        return f"Sources: {sources}"
