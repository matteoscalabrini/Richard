from richard.memory import MemoryStore


def test_add_returns_memory_with_id():
    store = MemoryStore(":memory:")
    m = store.add("likes tea")
    assert m.id >= 1
    assert m.text == "likes tea"
    assert m.person == "you"
    assert m.created_at  # non-empty ISO string


def test_all_returns_in_insertion_order():
    store = MemoryStore(":memory:")
    store.add("a")
    store.add("b")
    assert [m.text for m in store.all()] == ["a", "b"]


def test_remove_existing_then_missing():
    store = MemoryStore(":memory:")
    m = store.add("x")
    assert store.remove(m.id) is True
    assert store.remove(m.id) is False
    assert store.all() == []


def test_person_default_and_filter():
    store = MemoryStore(":memory:")
    store.add("yours")
    store.add("theirs", person="guest")
    assert [m.text for m in store.all(person="you")] == ["yours"]
    assert [m.text for m in store.all(person="guest")] == ["theirs"]


def test_persistence_across_reopen(tmp_path):
    path = tmp_path / "memory.db"
    store = MemoryStore(path)
    store.add("durable")
    store.close()
    reopened = MemoryStore(path)
    assert [m.text for m in reopened.all()] == ["durable"]
