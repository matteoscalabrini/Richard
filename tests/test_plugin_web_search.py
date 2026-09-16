import pytest

from richard.errors import WebSearchError
from richard.plugins.base import PluginContext
from richard.plugins.web_search import WebSearchPlugin
from richard.plugins.web_search.provider import WebSearchProvider
from richard.plugins.web_search.tavily import Hit, SearchResult


class StubClient:
    def __init__(self, result=None, error=None):
        self.result = result
        self.error = error
        self.calls = []

    def search(self, query, *, recent=False, max_results=3):
        self.calls.append({"query": query, "recent": recent, "max_results": max_results})
        if self.error:
            raise self.error
        return self.result


def test_context_line():
    provider = WebSearchProvider(StubClient())
    assert provider.context() == (
        "Web search is available with the web_search tool for facts you do not know "
        "or that may have changed; report what the tool returned and name the source, "
        "never assert beyond it."
    )


def test_schema_has_query_and_recent():
    provider = WebSearchProvider(StubClient())
    schemas = provider.schemas()
    assert len(schemas) == 1
    fn = schemas[0]["function"]
    assert fn["name"] == "web_search"
    props = fn["parameters"]["properties"]
    assert "query" in props and props["query"]["type"] == "string"
    assert fn["parameters"]["required"] == ["query"]
    assert props["recent"]["type"] == "boolean"


def test_execute_with_answer_formats_answer_and_sources_without_snippets():
    result = SearchResult(
        answer="It opened in 1981.",
        results=[
            Hit(title="Merrily", url="https://x/y", content="A very long snippet " * 20),
            Hit(title="Other", url="https://a/b", content="ignored"),
        ],
    )
    provider = WebSearchProvider(StubClient(result=result))
    output = provider.execute("web_search", {"query": "when did it open"})
    assert output == (
        "It opened in 1981.\n"
        "Sources: 1. Merrily — https://x/y; 2. Other — https://a/b"
    )


def test_execute_without_answer_uses_top_three_snippets_truncated():
    long_content = "x" * 500
    result = SearchResult(
        answer=None,
        results=[
            Hit(title="A", url="https://a", content=long_content),
            Hit(title="B", url="https://b", content="short b"),
            Hit(title="C", url="https://c", content="short c"),
            Hit(title="D", url="https://d", content="should be dropped"),
        ],
    )
    provider = WebSearchProvider(StubClient(result=result))
    output = provider.execute("web_search", {"query": "q"})
    assert "D" not in output
    assert f"1. A — https://a: {long_content[:200]}" in output
    assert "2. B — https://b: short b" in output
    assert "3. C — https://c: short c" in output


def test_execute_passes_recent_flag_through():
    stub = StubClient(result=SearchResult(answer=None, results=[]))
    provider = WebSearchProvider(stub)
    provider.execute("web_search", {"query": "news today", "recent": True})
    assert stub.calls[0] == {"query": "news today", "recent": True, "max_results": 3}


def test_execute_reports_errors_from_the_client():
    stub = StubClient(error=WebSearchError("web search unreachable"))
    provider = WebSearchProvider(stub)
    output = provider.execute("web_search", {"query": "q"})
    assert output == "web search unreachable"


def test_plugin_config_defaults():
    plugin = WebSearchPlugin()
    assert plugin.name == "web_search"
    assert plugin.config_defaults() == {"api_key_file": "~/.richard/tavily.key", "max_results": 3}


def test_plugin_build_reads_key_file_and_returns_one_provider(tmp_path):
    key_file = tmp_path / "tavily.key"
    key_file.write_text("  secret-key  \n")
    plugin = WebSearchPlugin(client_factory=lambda api_key, **kw: StubClient())
    ctx = PluginContext(
        config={"api_key_file": str(key_file), "max_results": 3},
        persona_name="R", data_dir=tmp_path, write=lambda s: None,
    )
    parts = plugin.build(ctx)
    assert [type(p).__name__ for p in parts.providers] == ["WebSearchProvider"]
    assert parts.context is None


def test_plugin_build_passes_the_stripped_key_to_the_client_factory(tmp_path):
    key_file = tmp_path / "tavily.key"
    key_file.write_text("  secret-key  \n")
    seen = {}

    def factory(api_key, **kwargs):
        seen["api_key"] = api_key
        return StubClient()

    plugin = WebSearchPlugin(client_factory=factory)
    ctx = PluginContext(
        config={"api_key_file": str(key_file), "max_results": 3},
        persona_name="R", data_dir=tmp_path, write=lambda s: None,
    )
    plugin.build(ctx)
    assert seen["api_key"] == "secret-key"


def test_plugin_build_raises_when_key_file_is_missing(tmp_path):
    missing = tmp_path / "nope.key"
    plugin = WebSearchPlugin()
    ctx = PluginContext(
        config={"api_key_file": str(missing), "max_results": 3},
        persona_name="R", data_dir=tmp_path, write=lambda s: None,
    )
    with pytest.raises(ValueError, match=str(missing)):
        plugin.build(ctx)


def test_plugin_build_raises_when_key_file_is_empty(tmp_path):
    key_file = tmp_path / "tavily.key"
    key_file.write_text("   \n")
    plugin = WebSearchPlugin()
    ctx = PluginContext(
        config={"api_key_file": str(key_file), "max_results": 3},
        persona_name="R", data_dir=tmp_path, write=lambda s: None,
    )
    with pytest.raises(ValueError, match=str(key_file)):
        plugin.build(ctx)


def test_entry_point_is_registered():
    from importlib import metadata

    names = {ep.name: ep.value for ep in metadata.entry_points(group="richard.plugins")}
    assert names["web_search"] == "richard.plugins.web_search:WebSearchPlugin"
