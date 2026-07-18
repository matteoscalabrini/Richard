from richard.memory import MemoryStore
from richard.memory_tools import MemoryTools


def test_schemas_lists_remember_and_forget():
    tools = MemoryTools(MemoryStore(":memory:"))
    names = [s["function"]["name"] for s in tools.schemas()]
    assert names == ["remember", "forget"]


def test_execute_remember_adds_to_store():
    store = MemoryStore(":memory:")
    tools = MemoryTools(store)
    assert tools.execute("remember", {"text": "likes tea"}) == "Remembered."
    assert [m.text for m in store.all()] == ["likes tea"]


def test_execute_remember_without_text():
    tools = MemoryTools(MemoryStore(":memory:"))
    assert "Nothing to remember" in tools.execute("remember", {})


def test_execute_forget_removes_then_reports_missing():
    store = MemoryStore(":memory:")
    tools = MemoryTools(store)
    m = store.add("x")
    assert tools.execute("forget", {"id": m.id}) == "Forgotten."
    assert tools.execute("forget", {"id": m.id}) == f"No memory with id {m.id}."


def test_execute_forget_bad_id():
    tools = MemoryTools(MemoryStore(":memory:"))
    assert "Invalid memory id" in tools.execute("forget", {"id": "abc"})


def test_execute_unknown_tool():
    tools = MemoryTools(MemoryStore(":memory:"))
    assert tools.execute("bogus", {}) == "Unknown tool: bogus."


def test_execute_remember_whitespace_only_is_rejected():
    store = MemoryStore(":memory:")
    tools = MemoryTools(store)
    assert "Nothing to remember" in tools.execute("remember", {"text": "   "})
    assert store.all() == []


def test_execute_remember_strips_text():
    store = MemoryStore(":memory:")
    tools = MemoryTools(store)
    tools.execute("remember", {"text": "  likes tea  "})
    assert [m.text for m in store.all()] == ["likes tea"]
