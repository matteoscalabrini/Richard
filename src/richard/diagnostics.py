"""Proactive diagnostics over the live Home Assistant inventory.

Richard's cached view of the world tells you what was seen, not what is true right
now. This module is the layer that goes and looks — fetching the live Home Assistant
inventory and reading individual entities on demand. It backs the LLM's diagnostic
tools, so "check my devices" does real work instead of trusting a stale list.

Nothing here is persisted: Home Assistant entities are never copied into SQLite.
"""

from __future__ import annotations

from dataclasses import dataclass

from richard.verification import (
    HOME_ASSISTANT,
    VerificationResult,
    VerificationStatus,
    compare_fields,
    render_values,
)


def _key(identifier: str) -> str:
    return f"ha:{identifier}"


@dataclass(frozen=True)
class Match:
    """One resolved Home Assistant entity."""

    identifier: str
    name: str

    @property
    def key(self) -> str:
        return _key(self.identifier)


@dataclass(frozen=True)
class RefreshReport:
    status: str  # ok | partial
    ha_configured: bool = False
    ha_reachable: bool = False
    ha_entity_count: int = 0
    ha_error: str | None = None

    def summary(self) -> str:
        if not self.ha_configured:
            return "Home Assistant is not configured."
        if self.ha_error:
            return f"Home Assistant: unreachable ({self.ha_error})"
        return f"Home Assistant: {self.ha_entity_count} entities"


@dataclass(frozen=True)
class Diagnosis:
    target: str | None = None
    name: str | None = None
    reachable: bool = False
    state: dict | None = None
    error: str | None = None
    ambiguous: tuple[str, ...] = ()

    def message(self) -> str:
        if self.ambiguous:
            return (
                f"'{self.name}' matches more than one target: {', '.join(self.ambiguous)}. "
                "Ask which one before doing anything."
            )
        if self.target is None:
            return self.error or "I don't see that target."
        head = f"{self.name} ({_key(self.target)}) via Home Assistant API"
        if not self.reachable:
            return f"{head}: not responding — {self.error}."
        state = ", ".join(f"{k}={v}" for k, v in (self.state or {}).items()) or "no state"
        return f"{head}: responding. State: {state}."


def _tool(name: str, description: str, properties: dict, required: list[str]) -> dict:
    return {
        "type": "function",
        "function": {
            "name": name,
            "description": description,
            "parameters": {
                "type": "object",
                "properties": properties,
                "required": required,
            },
        },
    }


REFRESH_SCHEMA = _tool(
    "refresh_devices",
    "Fetch the live Home Assistant inventory for real. Use when asked to refresh, "
    "or when the cached list looks stale or wrong.",
    {},
    [],
)

DIAGNOSE_SCHEMA = _tool(
    "diagnose_target",
    "Inspect one entity: its exact identifier, whether it responds right now, its "
    "live state, and any error. Use when asked why something didn't work.",
    {
        "target": {"type": "string", "description": "An entity id or friendly name."},
    },
    ["target"],
)

VERIFY_SCHEMA = _tool(
    "verify_target_state",
    "Read a target's live state and check it against what you expect, without changing "
    "anything. Use to confirm a claim before making it.",
    {
        "target": {"type": "string", "description": "An entity id or friendly name."},
        "expected": {
            "type": "object",
            "description": (
                "The state to check for: state and/or attribute names such as "
                "temperature or percentage."
            ),
            "additionalProperties": True,
        },
    },
    ["target", "expected"],
)


class DiagnosticsService:
    """Live checks over the Home Assistant inventory."""

    def __init__(self, home_assistant=None) -> None:
        self._home_assistant = home_assistant

    # --- refresh ---

    def refresh(self) -> RefreshReport:
        """Fetch the live inventory and report. Blocking by design — callers run it
        off the event loop."""
        if self._home_assistant is None:
            return RefreshReport(status="partial", ha_configured=False)
        try:
            entities = self._home_assistant.list_entities()
        except Exception as exc:  # noqa: BLE001 — an unreachable HA is a report, not a crash
            return RefreshReport(
                status="partial", ha_configured=True, ha_reachable=False, ha_error=str(exc)
            )
        return RefreshReport(
            status="ok",
            ha_configured=True,
            ha_reachable=True,
            ha_entity_count=len(entities),
        )

    # --- resolution ---

    def _entities(self) -> tuple[list, str | None]:
        if self._home_assistant is None:
            return [], None
        try:
            return list(self._home_assistant.list_entities()), None
        except Exception as exc:  # noqa: BLE001
            return [], str(exc)

    def _resolve(self, target: str) -> tuple[list[Match], str | None]:
        """Find a target in the inventory. Exact id/name matches win outright;
        only if none match does it fall back to a substring search."""
        needle = target.strip().lower()
        if not needle:
            return [], "An entity id or name is required."
        entities, ha_error = self._entities()

        exact: list[Match] = []
        partial: list[Match] = []
        for entity in entities:
            match = Match(entity.entity_id, entity.name)
            if needle in {entity.entity_id.lower(), entity.name.lower(), match.key.lower()}:
                exact.append(match)
            elif needle in entity.entity_id.lower() or needle in entity.name.lower():
                partial.append(match)
        return (exact or partial), ha_error

    # --- diagnose ---

    def diagnose(self, target: str) -> Diagnosis:
        matches, ha_error = self._resolve(target)
        if not matches:
            detail = f", and could not check Home Assistant: {ha_error}" if ha_error else ""
            return Diagnosis(name=target, error=f"I don't see a target called '{target}'{detail}.")
        if len(matches) > 1:
            return Diagnosis(
                name=target,
                ambiguous=tuple(f"{m.name} ({m.key})" for m in matches[:8]),
            )
        return self._diagnose_entity(matches[0])

    def _diagnose_entity(self, match: Match) -> Diagnosis:
        from richard.providers.home_assistant import entity_snapshot

        try:
            entity = self._home_assistant.get_entity(match.identifier)
        except Exception as exc:  # noqa: BLE001
            return Diagnosis(
                target=match.identifier, name=match.name,
                reachable=False, error=str(exc),
            )
        return Diagnosis(
            target=entity.entity_id, name=entity.name,
            reachable=True, state=entity_snapshot(entity),
        )

    # --- verify ---

    def verify(self, target: str, expected: dict) -> VerificationResult:
        """Read a target's live state and compare it to `expected`. Never writes."""
        matches, _ha_error = self._resolve(target)
        if not matches:
            return _failed(target, f"I don't see a target called '{target}'")
        if len(matches) > 1:
            choices = ", ".join(f"{m.name} ({m.key})" for m in matches[:8])
            return _failed(target, f"'{target}' matches more than one target: {choices}")
        return self._verify_entity(matches[0], expected)

    def _verify_entity(self, match: Match, expected: dict) -> VerificationResult:
        from richard.providers.home_assistant import entity_snapshot

        def build(status, **kwargs):
            return VerificationResult(
                status=status, source=HOME_ASSISTANT, target=match.identifier,
                name=match.name, action="verify_target_state", requested=expected, **kwargs,
            )

        try:
            entity = self._home_assistant.get_entity(match.identifier)
        except Exception as exc:  # noqa: BLE001
            return build(
                VerificationStatus.UNCONFIRMED,
                reason=f"{match.name} could not be read ({exc})",
            )
        return _judge(build, expected, entity_snapshot(entity), match.name)


def _judge(build, expected: dict, observed: dict, name: str) -> VerificationResult:
    mismatched, missing = compare_fields(expected, observed, {})
    seen = {key: observed.get(key) for key in expected if key in observed}
    if missing:
        return build(
            VerificationStatus.UNCONFIRMED,
            observed=seen,
            reason=f"{name} does not report {', '.join(missing)}",
        )
    if mismatched:
        return build(VerificationStatus.MISMATCH, observed=seen)
    return build(
        VerificationStatus.CONFIRMED,
        observed=seen,
        summary=f"{name} matches: {render_values(seen)}.",
    )


def _failed(target: str, reason: str) -> VerificationResult:
    return VerificationResult(
        status=VerificationStatus.FAILED, source=HOME_ASSISTANT, target=target, name=target,
        action="verify_target_state", requested={}, reason=reason,
    )


class DiagnosticsProvider:
    """The LLM's window into the diagnostics service."""

    def __init__(self, service: DiagnosticsService) -> None:
        self._service = service

    def schemas(self) -> list[dict]:
        return [REFRESH_SCHEMA, DIAGNOSE_SCHEMA, VERIFY_SCHEMA]

    def context(self) -> str | None:
        return (
            "You can check devices for real instead of trusting the cached list. "
            "refresh_devices fetches the live inventory, diagnose_target inspects one "
            "target, and verify_target_state checks a target's live state without "
            "changing it. Use them proactively for requests like 'check my devices' or "
            "'why didn't the kitchen light turn on'.\n"
            "Tool results prefixed CONFIRMED, MISMATCH, UNCONFIRMED, or FAILED are "
            "authoritative and override your own expectations. Only CONFIRMED lets you "
            "say something worked. Relay MISMATCH, UNCONFIRMED, and FAILED honestly — "
            "say what was observed and that it is not confirmed."
        )

    def execute(self, name: str, arguments: dict) -> str:
        if name == "refresh_devices":
            return self._service.refresh().summary()
        target = str(arguments.get("target", ""))
        if name == "diagnose_target":
            return self._service.diagnose(target).message()
        if name == "verify_target_state":
            expected = arguments.get("expected")
            if not isinstance(expected, dict) or not expected:
                return "'expected' must be a non-empty object of fields to check."
            return self._service.verify(target, expected).message()
        return f"Unknown tool: {name}."
