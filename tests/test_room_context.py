from richard.satellite.room import RoomContextProvider


def test_context_names_the_room():
    ctx = RoomContextProvider("Kitchen").context()
    assert "Kitchen" in ctx
    assert "here" in ctx.lower()


def test_no_room_means_no_context():
    assert RoomContextProvider(None).context() is None


def test_has_no_tools():
    p = RoomContextProvider("Kitchen")
    assert p.schemas() == []
    assert p.execute("anything", {}).startswith("Unknown tool")
