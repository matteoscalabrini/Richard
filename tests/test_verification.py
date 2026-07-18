from richard.verification import (
    Approx,
    VerificationResult,
    VerificationStatus,
    compare_fields,
    poll_until,
)


class FakeClock:
    """A monotonic clock that only advances when `sleep` is called, so polling tests
    exercise the real timing logic without spending real time."""

    def __init__(self) -> None:
        self.now = 0.0
        self.slept: list[float] = []

    def time(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.slept.append(seconds)
        self.now += seconds


# --- comparison ---


def test_compare_fields_matches_only_requested_fields():
    mismatched, missing = compare_fields(
        {"on": True, "speed": 40},
        {"running": True, "speedPercent": 40, "direction": "forward"},
        {"on": "running", "speed": "speedPercent", "direction": "direction"},
    )
    assert mismatched == [] and missing == []


def test_compare_fields_normalizes_booleans_ints_and_strings():
    mismatched, missing = compare_fields(
        {"on": True, "speed": 40, "direction": "Forward"},
        {"running": 1, "speedPercent": "40", "direction": "forward"},
        {"on": "running", "speed": "speedPercent", "direction": "direction"},
    )
    assert mismatched == [] and missing == []


def test_compare_fields_reports_mismatch():
    mismatched, missing = compare_fields(
        {"on": True, "speed": 40},
        {"running": True, "speedPercent": 80},
        {"on": "running", "speed": "speedPercent"},
    )
    assert mismatched == ["speed"] and missing == []


def test_compare_fields_reports_missing_required_field():
    mismatched, missing = compare_fields(
        {"on": True, "speed": 40},
        {"running": True},
        {"on": "running", "speed": "speedPercent"},
    )
    assert missing == ["speed"] and mismatched == []


def test_compare_fields_approx_absorbs_rounding():
    mismatched, _ = compare_fields(
        {"brightness": Approx(102, 2)}, {"brightness": 101}, {"brightness": "brightness"}
    )
    assert mismatched == []
    mismatched, _ = compare_fields(
        {"brightness": Approx(102, 2)}, {"brightness": 60}, {"brightness": "brightness"}
    )
    assert mismatched == ["brightness"]


# --- polling ---


def test_poll_until_returns_first_matching_read():
    reads = [{"state": "off"}, {"state": "off"}, {"state": "on"}]
    clock = FakeClock()
    outcome = poll_until(
        lambda: reads.pop(0),
        lambda value: value["state"] == "on",
        timeout=2.0,
        interval=0.25,
        clock=clock.time,
        sleep=clock.sleep,
    )
    assert outcome.matched is True
    assert outcome.value == {"state": "on"}
    assert clock.slept == [0.25, 0.25]  # no real sleeping


def test_poll_until_times_out_and_returns_last_value():
    clock = FakeClock()
    outcome = poll_until(
        lambda: {"state": "off"},
        lambda value: value["state"] == "on",
        timeout=1.0,
        interval=0.25,
        clock=clock.time,
        sleep=clock.sleep,
    )
    assert outcome.matched is False
    assert outcome.value == {"state": "off"}
    assert outcome.error is None
    assert clock.now == 1.0  # bounded by the timeout, not by the number of reads


def test_poll_until_surfaces_read_failure():
    def boom():
        raise RuntimeError("unreachable")

    clock = FakeClock()
    outcome = poll_until(
        boom, lambda value: True, timeout=0.5, interval=0.25, clock=clock.time, sleep=clock.sleep
    )
    assert outcome.matched is False
    assert outcome.value is None
    assert "unreachable" in outcome.error


# --- result formatting ---


def _result(status, **kwargs):
    base = dict(
        source="home_assistant",
        target="AA",
        name="Ceiling fan",
        action="set_fan",
        requested={"on": True, "speed": 40},
    )
    base.update(kwargs)
    return VerificationResult(status=status, **base)


def test_confirmed_message_uses_prefix_and_summary():
    result = _result(
        VerificationStatus.CONFIRMED,
        observed={"on": True, "speed": 40},
        summary="Ceiling fan is on at 40%.",
    )
    assert result.message() == "CONFIRMED: Ceiling fan is on at 40%."
    assert result.confirmed is True


def test_mismatch_message_reports_both_states_and_forbids_success_claim():
    message = _result(
        VerificationStatus.MISMATCH, observed={"on": True, "speed": 80}
    ).message()
    assert message.startswith("MISMATCH: ")
    assert "on=true, speed=40" in message  # requested
    assert "on=true, speed=80" in message  # observed
    assert "Do not claim success." in message


def test_unconfirmed_message_forbids_success_claim():
    message = _result(
        VerificationStatus.UNCONFIRMED, reason="read-back failed"
    ).message()
    assert message.startswith("UNCONFIRMED: ")
    assert "read-back failed" in message
    assert "Do not claim success." in message


def test_failed_message_forbids_success_claim():
    message = _result(VerificationStatus.FAILED, reason="isn't responding: nope").message()
    assert message.startswith("FAILED: ")
    assert "isn't responding: nope" in message
    assert "Do not claim success." in message


def test_only_confirmed_is_confirmed():
    for status in (
        VerificationStatus.MISMATCH,
        VerificationStatus.UNCONFIRMED,
        VerificationStatus.FAILED,
    ):
        assert _result(status).confirmed is False
