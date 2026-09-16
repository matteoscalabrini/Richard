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
    ctx = MemoryProvider(store).context()
    assert "- [1] " in ctx and "— takes tea with no sugar" in ctx


def test_context_renders_memory_timestamp_in_given_zone():
    store = MemoryStore(":memory:")
    store.add("likes tea")
    store._conn.execute(
        "UPDATE memories SET created_at = ? WHERE id = 1", ("2026-09-16T17:31:00+00:00",)
    )
    store._conn.commit()
    provider = MemoryProvider(store, tz_name="Europe/Rome")
    assert "- [1] 2026-09-16 19:31 — likes tea" in provider.context()
