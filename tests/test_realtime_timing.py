from richard.realtime.timing import TurnTiming


def test_marks_are_milliseconds_since_start_and_only_set_marks_are_reported():
    clock = iter([10.0, 10.4, 10.9, 11.3])
    timing = TurnTiming(now=lambda: next(clock))
    timing.start()
    timing.mark("stt")
    timing.mark("first_token")
    timing.mark("first_audio")
    assert timing.summary() == {"stt": 400, "first_token": 900, "first_audio": 1300}
    assert timing.line() == "turn timing: stt=400ms first_token=900ms first_audio=1300ms"


def test_first_mark_wins_and_marks_before_start_are_ignored():
    clock = iter([5.0, 5.1, 5.2])
    timing = TurnTiming(now=lambda: next(clock))
    timing.mark("first_token")  # no start yet: dropped
    timing.start()
    timing.mark("first_token")
    timing.mark("first_token")  # second call must not overwrite the first
    assert timing.summary() == {"first_token": 100}


def test_line_without_start_is_empty():
    assert TurnTiming().line() == ""
