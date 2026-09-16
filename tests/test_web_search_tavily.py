import json

import httpx
import pytest

from richard.errors import WebSearchError
from richard.plugins.web_search.tavily import Hit, SearchResult, TavilyClient


def _client(handler):
    transport = httpx.MockTransport(handler)
    return TavilyClient("secret-key", client=httpx.Client(transport=transport))


def test_search_recent_sends_bearer_auth_and_news_body():
    def handler(request):
        assert request.url == "https://api.tavily.com/search"
        assert request.headers["Authorization"] == "Bearer secret-key"
        assert json.loads(request.content) == {
            "query": "who won the game",
            "search_depth": "basic",
            "include_answer": "basic",
            "max_results": 3,
            "topic": "news",
            "time_range": "week",
        }
        return httpx.Response(
            200,
            json={
                "answer": "It opened in 1981.",
                "results": [
                    {"title": "Merrily", "url": "https://x/y", "content": "…"}
                ],
                "response_time": 0.4,
            },
        )

    result = _client(handler).search("who won the game", recent=True)
    assert result == SearchResult(
        answer="It opened in 1981.",
        results=[Hit(title="Merrily", url="https://x/y", content="…")],
    )


def test_search_not_recent_omits_time_range_and_uses_general_topic():
    def handler(request):
        body = json.loads(request.content)
        assert body["topic"] == "general"
        assert "time_range" not in body
        return httpx.Response(200, json={"answer": None, "results": []})

    result = _client(handler).search("what is a fugue")
    assert result == SearchResult(answer=None, results=[])


def test_search_passes_max_results():
    def handler(request):
        body = json.loads(request.content)
        assert body["max_results"] == 5
        return httpx.Response(200, json={"answer": None, "results": []})

    _client(handler).search("query", max_results=5)


def test_unauthorized_key_is_rejected():
    client = _client(lambda request: httpx.Response(401, request=request))
    with pytest.raises(WebSearchError, match="key was rejected"):
        client.search("query")


@pytest.mark.parametrize("status", [429, 432, 433])
def test_quota_exhausted_statuses(status):
    client = _client(lambda request: httpx.Response(status, request=request))
    with pytest.raises(WebSearchError, match="quota exhausted"):
        client.search("query")


def test_other_status_reports_http_code():
    client = _client(lambda request: httpx.Response(500, request=request))
    with pytest.raises(WebSearchError, match="web search failed: HTTP 500"):
        client.search("query")


def test_transport_error_is_unreachable():
    def handler(request):
        raise httpx.ConnectError("boom", request=request)

    client = _client(handler)
    with pytest.raises(WebSearchError, match="unreachable"):
        client.search("query")
