"""Proactive diagnostics over the live inventory of every target source.

Richard's cached view of the world tells you what was seen, not what is true right
now. This module is the layer that goes and looks — asking each enabled plugin's
target reader for its live inventory and reading individual targets on demand. It
backs the LLM's diagnostic tools, so "check my devices" does real work instead of
trusting a stale list.

Nothing here is persisted: targets are never copied into SQLite.
"""

from __future__ import annotations

from dataclasses import dataclass

from richard.plugins.base import TargetReader
from richard.verification import VerificationResult, VerificationStatus


@dataclass(frozen=True)
class Match:
    """One resolved target."""

    kind: str
    identifier: str
    name: str

    @property
    def key(self) -> str:
        return f"{self.kind}:{self.identifier}"


@dataclass(frozen=True)
class SourceReport:
    kind: str
    reachable: bool
    target_count: int = 0
    error: str | None = None


@dataclass(frozen=True)
class RefreshReport:
    status: str  # ok | partial
    sources: tuple[SourceReport, ...] = ()

    def summary(self) -> str:
        if not self.sources:
            return "No target sources are configured."
        parts = []
        for source in self.sources:
            if source.reachable:
                parts.append(f"{source.kind}: {source.target_count} targets")
            else:
                parts.append(f"{source.kind}: unreachable ({source.error})")
        return "; ".join(parts)


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
                f"'{self.target}' matches more than one target: {', '.join(self.ambiguous)}. "
                "Ask which one before doing anything."
            )
        if self.name is None:
            return self.error or "I don't see that target."
        head = f"{self.name} ({self.target})"
        if not self.reachable:
            return f"{head}: not responding — {self.error}."
        snapshot = self.state or {}
        details = [f"state={snapshot.get('state')}"]
        details.extend(f"{k}={v}" for k, v in (snapshot.get("attributes") or {}).items())
        return f"{head}: responding. State: {', '.join(details)}."


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


_TARGET_DESCRIPTION = "A target as kind:id (for example ha:light.kitchen) or a friendly name."

REFRESH_SCHEMA = _tool(
    "refresh_devices",
    "Fetch the live inventory of every connected target source for real. Use when "
    "asked to refresh, or when the cached list looks stale or wrong.",
    {},
    [],
)

DIAGNOSE_SCHEMA = _tool(
    "diagnose_target",
    "Inspect one target: its exact identifier, whether it responds right now, its "
    "live state, and any error. Use when asked why something didn't work.",
    {
        "target": {"type": "string", "description": _TARGET_DESCRIPTION},
    },
    ["target"],
)

VERIFY_SCHEMA = _tool(
    "verify_target_state",
    "Read a target's live state and check it against what you expect, without changing "
    "anything. Use to confirm a claim before making it.",
    {
        "target": {"type": "string", "description": _TARGET_DESCRIPTION},
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
    """Live checks over every target source the enabled plugins provide."""

    def __init__(self, readers: dict[str, TargetReader] | None = None) -> None:
        self._readers: dict[str, TargetReader] = dict(readers or {})

    # --- refresh ---

    def refresh(self) -> RefreshReport:
        """Ask each source for its live inventory and report. Blocking by design —
        callers run it off the event loop."""
        sources = []
        for kind, reader in self._readers.items():
            try:
                count = len(reader.list_targets())
            except Exception as exc:  # noqa: BLE001 — an unreachable source is a report, not a crash
                sources.append(SourceReport(kind=kind, reachable=False, error=str(exc)))
            else:
                sources.append(SourceReport(kind=kind, reachable=True, target_count=count))
        status = "ok" if all(s.reachable for s in sources) else "partial"
        return RefreshReport(status=status, sources=tuple(sources))

    # --- resolution ---

    def _resolve(self, target: str) -> tuple[list[Match], str | None]:
        """Find a target across every source. A `kind:id` key is looked up in that
        source only; otherwise exact id/name matches win outright, and only if none
        match does it fall back to a substring search."""
        kind, separator, identifier = target.partition(":")
        if separator and kind in self._readers:
            try:
                infos = self._readers[kind].list_targets()
            except Exception as exc:  # noqa: BLE001
                return [], str(exc)
            return [Match(kind, i.id, i.name) for i in infos if i.id == identifier], None
        if separator and kind and " " not in kind:
            return [], f"Unknown target source: {kind}"
        needle = target.strip().lower()
        if not needle:
            return [], "A target id or name is required."
        exact: list[Match] = []
        partial: list[Match] = []
        for kind_name, reader in self._readers.items():
            try:
                infos = reader.list_targets()
            except Exception as exc:  # noqa: BLE001
                return [], str(exc)
            for info in infos:
                match = Match(kind_name, info.id, info.name)
                if needle in {info.id.lower(), info.name.lower(), match.key.lower()}:
                    exact.append(match)
                elif needle in info.id.lower() or needle in info.name.lower():
                    partial.append(match)
        return (exact or partial), None

    # --- diagnose ---

    def diagnose(self, target: str) -> Diagnosis:
        matches, error = self._resolve(target)
        if error:
            return Diagnosis(target=target, error=error)
        if not matches:
            return Diagnosis(target=target, error=f"No target matches {target}.")
        if len(matches) > 1:
            return Diagnosis(target=target, ambiguous=tuple(m.key for m in matches[:8]), error="Ambiguous target.")
        match = matches[0]
        try:
            state = self._readers[match.kind].read(match.identifier)
        except Exception as exc:  # noqa: BLE001
            return Diagnosis(target=match.key, name=match.name, error=str(exc))
        return Diagnosis(target=match.key, name=match.name, reachable=True, state=state)

    # --- verify ---

    def verify(self, target: str, expected: dict) -> VerificationResult:
        """Read a target's live state and compare it to `expected`. Never writes."""
        matches, error = self._resolve(target)
        if error or not matches:
            return VerificationResult(
                status=VerificationStatus.FAILED, source="diagnostics", target=target, name=target,
                action="verify", reason=error or f"No target matches {target}.",
            )
        if len(matches) > 1:
            return VerificationResult(
                status=VerificationStatus.FAILED, source="diagnostics", target=target, name=target,
                action="verify", reason="Ambiguous target: " + ", ".join(m.key for m in matches[:8]),
            )
        match = matches[0]
        return self._readers[match.kind].verify(match.identifier, expected)


class DiagnosticsProvider:
    """The LLM's window into the diagnostics service."""

    def __init__(self, service: DiagnosticsService) -> None:
        self._service = service

    def schemas(self) -> list[dict]:
        return [REFRESH_SCHEMA, DIAGNOSE_SCHEMA, VERIFY_SCHEMA]

    def context(self) -> str | None:
        return (
            "You can check devices for real instead of trusting the cached list. "
            "refresh_devices fetches the live inventory of every connected target source, "
            "diagnose_target inspects one target, and verify_target_state checks a target's "
            "live state without changing it. Targets are kind:id (for example "
            "ha:light.kitchen) or a plain name. Use them proactively for requests like "
            "'check my devices' or 'why didn't the kitchen light turn on'.\n"
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
