from richard.realtime.registry import SessionRegistry


class S:
    def __init__(self):
        self.lines = []

    def add_context(self, line):
        self.lines.append(line)


def test_registry_offers_context_to_open_sessions_only():
    reg = SessionRegistry()
    assert reg.offer_context("someone entered") is False
    a, b = S(), S()
    reg.add(a); reg.add(b)
    assert reg.active() == [a, b]
    assert reg.offer_context("matteo recognised") is True
    assert a.lines == ["matteo recognised"] and b.lines == ["matteo recognised"]
    reg.remove(a)
    reg.remove(a)  # idempotent
    assert reg.active() == [b]


class W(S):
    def __init__(self):
        super().__init__()
        self.woken = 0

    def wake(self):
        self.woken += 1
        return True


def test_offer_context_can_wake_the_sessions():
    reg = SessionRegistry()
    a = W()
    reg.add(a)
    assert reg.offer_context("someone entered", wake=False) is True
    assert a.woken == 0
    assert reg.offer_context("matteo recognised", wake=True) is True
    assert a.lines == ["someone entered", "matteo recognised"] and a.woken == 1
