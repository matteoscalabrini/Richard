"""Shared verification primitives: did the device actually do what was asked?

Richard must never claim success on a state-changing command unless a fresh read-back
confirms it. This module holds the vocabulary for that — the four outcomes, the pure
expected-vs-observed comparison, bounded polling for asynchronous transitions, and the
prefixed tool-result strings the LLM sees. The networking stays in the clients and
controllers; nothing here touches a socket.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Callable

# Sources a verification can come from.
HOME_ASSISTANT = "home_assistant"


class VerificationStatus(str, Enum):
    CONFIRMED = "confirmed"
    MISMATCH = "mismatch"
    UNCONFIRMED = "unconfirmed"
    FAILED = "failed"


@dataclass(frozen=True)
class Approx:
    """An expected number that tolerates rounding — Home Assistant stores brightness
    as 0-255, so a brightness_pct round-trip lands a point or two off."""

    value: float
    tolerance: float = 0.0


_TRUE = {"1", "true", "on", "yes"}
_FALSE = {"0", "false", "off", "no"}


def _as_bool(value) -> bool | None:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    if isinstance(value, str):
        token = value.strip().lower()
        if token in _TRUE:
            return True
        if token in _FALSE:
            return False
    return None


def _as_number(value) -> float | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        try:
            return float(value.strip())
        except ValueError:
            return None
    return None


def values_match(expected, observed) -> bool:
    """Compare one expected value against what the device reported, normalizing against
    the *expected* type: a bool accepts 1/"on"/True, a number accepts "40", and anything
    else compares as a case-insensitive string."""
    if isinstance(expected, Approx):
        number = _as_number(observed)
        return number is not None and abs(number - expected.value) <= expected.tolerance
    if isinstance(expected, bool):
        return _as_bool(observed) is expected
    if isinstance(expected, (int, float)):
        number = _as_number(observed)
        return number is not None and number == float(expected)
    return str(expected).strip().lower() == str(observed).strip().lower()


def compare_fields(
    requested: dict, observed: dict, field_map: dict[str, str]
) -> tuple[list[str], list[str]]:
    """Compare only the fields the caller requested.

    Returns (mismatched, missing): `mismatched` fields came back with a different value,
    `missing` fields the target never reported — which means unconfirmed, not success.
    """
    mismatched: list[str] = []
    missing: list[str] = []
    for name, expected in requested.items():
        if expected is None:
            continue
        key = field_map.get(name, name)
        if key not in observed or observed[key] is None:
            missing.append(name)
        elif not values_match(expected, observed[key]):
            mismatched.append(name)
    return mismatched, missing


@dataclass(frozen=True)
class PollOutcome:
    value: object | None
    matched: bool
    error: str | None = None


def poll_until(
    read: Callable[[], object],
    matches: Callable[[object], bool],
    *,
    timeout: float = 2.0,
    interval: float = 0.25,
    clock: Callable[[], float] = time.monotonic,
    sleep: Callable[[float], None] = time.sleep,
) -> PollOutcome:
    """Read until `matches` holds or the timeout expires, whichever comes first.

    Home Assistant applies some services asynchronously, so an immediate read can catch
    the old state. Always reads at least once. Clock and sleep are injectable so tests
    exercise the timing without spending real time.
    """
    deadline = clock() + timeout
    value: object | None = None
    error: str | None = None
    while True:
        try:
            value = read()
            error = None
            if matches(value):
                return PollOutcome(value=value, matched=True)
        except Exception as exc:  # noqa: BLE001 — a failed read is an outcome, not a crash
            value = None
            error = str(exc)
        remaining = deadline - clock()
        if remaining <= 0:
            return PollOutcome(value=value, matched=False, error=error)
        sleep(min(interval, remaining))


def render_values(values: dict | None) -> str:
    if not values:
        return "nothing"
    parts = []
    for key, value in values.items():
        if isinstance(value, Approx):
            rendered = f"~{value.value:g}"
        elif isinstance(value, bool):
            rendered = "true" if value else "false"
        else:
            rendered = str(value)
        parts.append(f"{key}={rendered}")
    return ", ".join(parts)


@dataclass(frozen=True)
class VerificationResult:
    """The authoritative outcome of one state-changing command or state check."""

    status: VerificationStatus
    source: str
    target: str
    name: str
    action: str
    requested: dict = field(default_factory=dict)
    observed: dict | None = None
    reason: str | None = None
    # A human sentence for the confirmed case, e.g. "Desk lamp is on at 40%."
    summary: str | None = None

    @property
    def confirmed(self) -> bool:
        return self.status is VerificationStatus.CONFIRMED

    def message(self) -> str:
        """The exact string handed to the LLM. The prefix is load-bearing: only
        CONFIRMED permits a success claim, so every other outcome says so out loud."""
        if self.status is VerificationStatus.CONFIRMED:
            body = self.summary or (
                f"{self.name} matches the requested state ({render_values(self.requested)})."
            )
            return f"CONFIRMED: {body}"
        if self.status is VerificationStatus.MISMATCH:
            return (
                f"MISMATCH: {self.name} is not in the requested state. "
                f"Requested {render_values(self.requested)}; observed {render_values(self.observed)}. "
                "Do not claim success."
            )
        if self.status is VerificationStatus.UNCONFIRMED:
            reason = self.reason or "the resulting state could not be read back"
            observed = (
                f" Last known state: {render_values(self.observed)}." if self.observed else ""
            )
            return (
                f"UNCONFIRMED: {self.name} accepted {self.action} but the result could "
                f"not be verified ({reason}).{observed} Do not claim success."
            )
        reason = self.reason or "the command was rejected"
        return f"FAILED: {self.name} — {reason}. Do not claim success."


def judge(build, expected: dict, observed: dict, name: str) -> VerificationResult:
    """Grade an observed snapshot against the expected fields. `build(status, **kwargs)`
    produces the VerificationResult so the caller owns source/target/action."""
    mismatched, missing = compare_fields(expected, observed, {})
    seen = {key: observed.get(key) for key in expected if key in observed}
    if missing:
        return build(
            VerificationStatus.UNCONFIRMED,
            observed=seen,
            reason=f"{name} does not report {', '.join(missing)}",
        )
    if mismatched:
        return build(
            VerificationStatus.MISMATCH,
            observed=seen,
            reason="mismatch on " + ", ".join(mismatched),
        )
    return build(
        VerificationStatus.CONFIRMED,
        observed=seen,
        summary=f"{name} matches: {render_values(seen)}.",
    )


def failed(source: str, target: str, name: str, action: str, reason: str) -> VerificationResult:
    return VerificationResult(
        status=VerificationStatus.FAILED, source=source, target=target, name=name,
        action=action, reason=reason,
    )
