from __future__ import annotations

from dataclasses import dataclass, field

import httpx

from richard.errors import WebSearchError

_URL = "https://api.tavily.com/search"


@dataclass(frozen=True)
class Hit:
    title: str
    url: str
    content: str


@dataclass(frozen=True)
class SearchResult:
    answer: str | None
    results: list[Hit] = field(default_factory=list)


class TavilyClient:
    """Small client for Tavily's bearer-authenticated search API."""

    def __init__(
        self,
        api_key: str,
        *,
        timeout: float = 8.0,
        client: httpx.Client | None = None,
    ) -> None:
        self._api_key = api_key
        self._client = client or httpx.Client(timeout=httpx.Timeout(timeout, connect=3.0))

    @property
    def _headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self._api_key}"}

    def search(self, query: str, *, recent: bool = False, max_results: int = 3) -> SearchResult:
        body = {
            "query": query,
            "search_depth": "basic",
            "include_answer": "basic",
            "max_results": max_results,
            "topic": "general",
        }
        if recent:
            body["time_range"] = "week"
        payload = self._request(body)
        results = payload.get("results")
        hits = [
            Hit(title=str(item.get("title", "")), url=str(item.get("url", "")), content=str(item.get("content", "")))
            for item in results
            if isinstance(item, dict)
        ] if isinstance(results, list) else []
        return SearchResult(answer=payload.get("answer"), results=hits)

    def _request(self, body: dict) -> dict:
        try:
            response = self._client.post(_URL, headers=self._headers, json=body)
            response.raise_for_status()
            return response.json()
        except httpx.HTTPStatusError as exc:
            status = exc.response.status_code
            if status == 401:
                message = "the web search key was rejected"
            elif status in (429, 432, 433):
                message = "web search quota exhausted"
            else:
                message = f"web search failed: HTTP {status}"
            raise WebSearchError(message) from exc
        except httpx.HTTPError as exc:
            raise WebSearchError("web search unreachable") from exc
