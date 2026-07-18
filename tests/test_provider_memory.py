from richard.memory import MemoryStore
from richard.providers.memory import MemoryProvider


def test_schemas_are_remember_and_forget():
    p = MemoryProvider(MemoryStore(":memory:"))
    assert [s["function"]["name"] for s in p.schemas()] == ["remember", "forget"]


def test_execute_delegates_to_store():
    store = MemoryStore(":memory:")
    p = MemoryProvider(store)
    assert p.execute("remember", {"text": "likes tea"}) == "Remembered."
    assert [m.text for m in store.all()] == ["likes tea"]


def test_context_empty_says_nothing_yet():
    p = MemoryProvider(MemoryStore(":memory:"))
    ctx = p.context()
    assert "remember tool" in ctx
    assert "Nothing yet." in ctx


def test_context_lists_memories_with_ids():
    store = MemoryStore(":memory:")
    store.add("takes tea with no sugar")
    assert "- [1] takes tea with no sugar" in MemoryProvider(store).context()
