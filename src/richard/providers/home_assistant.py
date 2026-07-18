from __future__ import annotations

import json
import re
import time
from datetime import datetime, timezone
from typing import Callable

from richard.errors import HomeAssistantError
from richard.home_assistant import HomeAssistantClient, HomeAssistantEntity
from richard.verification import (
    HOME_ASSISTANT,
    Approx,
    VerificationResult,
    VerificationStatus,
    compare_fields,
    poll_until,
)


LIST_ENTITIES_SCHEMA = {
    "type": "function",
    "function": {
        "name": "list_home_assistant_entities",
        "description": (
            "List entities exposed by Home Assistant. Filter by domain or a name/id query "
            "before controlling an entity."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "domain": {
                    "type": "string",
                    "description": "Optional entity domain, e.g. light, switch, fan, or sensor.",
                },
                "query": {
                    "type": "string",
                    "description": "Optional text found in the friendly name or entity id.",
                },
            },
        },
    },
}

GET_STATE_SCHEMA = {
    "type": "function",
    "function": {
        "name": "get_home_assistant_state",
        "description": "Get the current state and attributes of one Home Assistant entity.",
        "parameters": {
            "type": "object",
            "properties": {
                "entity": {
                    "type": "string",
                    "description": "An entity id or friendly name, e.g. light.kitchen.",
                }
            },
            "required": ["entity"],
        },
    },
}

CALL_SERVICE_SCHEMA = {
    "type": "function",
    "function": {
        "name": "call_home_assistant_service",
        "description": (
            "Control one Home Assistant entity by calling a service in that entity's domain. "
            "Examples: turn_on, turn_off, toggle, set_percentage, set_temperature, "
            "open_cover, or close_cover."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "entity": {
                    "type": "string",
                    "description": "An exact entity id or an unambiguous friendly name.",
                },
                "service": {
                    "type": "string",
                    "description": "Service name without the domain, e.g. turn_on.",
                },
                "data": {
                    "type": "object",
                    "description": (
                        "Optional service data such as brightness_pct, percentage, temperature, "
                        "hvac_mode, or position. entity_id is supplied automatically."
                    ),
                    "additionalProperties": True,
                },
            },
            "required": ["entity", "service"],
        },
    },
}

_SERVICE_RE = re.compile(r"^[a-z][a-z0-9_]*$")
_MAX_LIST_RESULTS = 50
_MAX_STALE_RESULTS = 10
_MAX_ATTRIBUTES_CHARS = 2000
_STALE_STATES = ("unavailable", "unknown")


def _relative_age(timestamp: str | None, now: datetime) -> str | None:
    if not timestamp:
        return None
    try:
        moment = datetime.fromisoformat(timestamp)
    except ValueError:
        return None
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    minutes = int((now - moment).total_seconds() // 60)
    if minutes < 0:
        return None
    if minutes < 60:
        return f"{minutes}m"
    hours = minutes // 60
    if hours < 48:
        return f"{hours}h"
    return f"{hours // 24}d"


def _entity_line(entity: HomeAssistantEntity, now: datetime | None = None) -> str:
    unit = entity.attributes.get("unit_of_measurement")
    value = f"{entity.state}{unit}" if unit else entity.state
    if entity.state in _STALE_STATES and now is not None:
        age = _relative_age(entity.last_changed, now)
        if age:
            value += f" (since {age} ago)"
    return f"{entity.name} ({entity.entity_id}): {value}"


def _entity_detail(entity: HomeAssistantEntity, now: datetime | None = None) -> str:
    attributes = {
        key: value
        for key, value in entity.attributes.items()
        if key not in {"entity_picture", "attribution"}
    }
    encoded = json.dumps(attributes, ensure_ascii=False, sort_keys=True, default=str)
    if len(encoded) > _MAX_ATTRIBUTES_CHARS:
        encoded = encoded[:_MAX_ATTRIBUTES_CHARS] + "…"
    return f"{_entity_line(entity, now)}; attributes={encoded}"


def entity_snapshot(entity: HomeAssistantEntity) -> dict:
    """One flat namespace for comparison: the entity's state plus its attributes.
    `state` wins the name, which is why expected objects spell attributes out by name."""
    return {"state": entity.state, **entity.attributes}


def _expected_effect(service: str, data: dict, pre: HomeAssistantEntity | None) -> dict | None:
    """What a supported service should leave behind, or None when Richard cannot infer
    it. Guessing at an arbitrary service's semantics would manufacture a success claim,
    so unknown services deliberately fall through to None (reported as unconfirmed)."""
    expected: dict = {}
    if service == "turn_on":
        expected["state"] = "on"
        if "brightness_pct" in data:
            percent = _as_number(data["brightness_pct"])
            if percent is None:
                return None
            # Home Assistant stores brightness on a 0-255 scale, so a percentage
            # round-trips with a point or two of rounding error.
            expected["brightness"] = Approx(round(255 * percent / 100), 2)
    elif service == "turn_off":
        expected["state"] = "off"
    elif service == "toggle":
        if pre is None or pre.state not in ("on", "off"):
            return None
        expected["state"] = "off" if pre.state == "on" else "on"
    elif service == "open_cover":
        expected["state"] = "open"
    elif service == "close_cover":
        expected["state"] = "closed"
    elif service == "lock":
        expected["state"] = "locked"
    elif service == "unlock":
        expected["state"] = "unlocked"
    elif service in ("set_percentage", "set_temperature"):
        attribute = service[len("set_") :]
        if attribute not in data:
            return None
        expected[attribute] = data[attribute]
    else:
        return None
    return expected


def _as_number(value) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _observed(entity: HomeAssistantEntity, expected: dict) -> dict:
    """Only the fields that were checked, so the message stays readable."""
    snapshot = entity_snapshot(entity)
    return {key: snapshot.get(key) for key in expected}


def _result(entity: HomeAssistantEntity, action: str, requested: dict):
    def build(status: VerificationStatus, **kwargs) -> VerificationResult:
        return VerificationResult(
            status=status,
            source=HOME_ASSISTANT,
            target=entity.entity_id,
            name=entity.name,
            action=action,
            requested=requested,
            **kwargs,
        )

    return build


class HomeAssistantProvider:
    def __init__(
        self,
        client: HomeAssistantClient,
        *,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
        now: Callable[[], datetime] | None = None,
        poll_timeout: float = 2.0,
        poll_interval: float = 0.25,
    ) -> None:
        self._client = client
        self._clock = clock
        self._sleep = sleep
        self._now = now or (lambda: datetime.now(timezone.utc))
        self._poll_timeout = poll_timeout
        self._poll_interval = poll_interval

    @property
    def client(self) -> HomeAssistantClient:
        return self._client

    def schemas(self) -> list[dict]:
        return [LIST_ENTITIES_SCHEMA, GET_STATE_SCHEMA, CALL_SERVICE_SCHEMA]

    def context(self) -> str | None:
        return (
            "Home Assistant is connected. Its devices appear as entities. Use the Home "
            "Assistant tools to discover entity ids, inspect live state, and control them. "
            "Do not invent an entity id or claim an action succeeded without a tool result. "
            "A service call returns a verified outcome: only CONFIRMED means it worked. "
            "Relay MISMATCH, UNCONFIRMED, and FAILED honestly instead of claiming success. "
            "An unavailable or unknown state does not mean a device is broken: battery "
            "sensors (door/window, buttons) sleep between check-ins, and some entities are "
            "leftovers from removed devices. Report how long the state has been stale "
            "(the 'since' age) instead of declaring the device offline."
        )

    def execute(self, name: str, arguments: dict) -> str:
        try:
            if name == "list_home_assistant_entities":
                return self._list(arguments)
            if name == "get_home_assistant_state":
                entity, error = self._resolve(str(arguments.get("entity", "")))
                return error or _entity_detail(entity, self._now())
            if name == "call_home_assistant_service":
                return self._call(arguments)
        except HomeAssistantError as exc:
            return str(exc)
        return f"Unknown tool: {name}."

    def _list(self, arguments: dict) -> str:
        domain = str(arguments.get("domain", "")).strip().lower().rstrip(".")
        query = str(arguments.get("query", "")).strip().lower()
        entities = self._client.list_entities()
        if domain:
            entities = [entity for entity in entities if entity.domain == domain]
        if query:
            entities = [
                entity
                for entity in entities
                if query in entity.entity_id.lower() or query in entity.name.lower()
            ]
        entities.sort(key=lambda entity: (entity.name.lower(), entity.entity_id))
        available = [entity for entity in entities if entity.state not in _STALE_STATES]
        stale = [entity for entity in entities if entity.state in _STALE_STATES]
        if not available and not stale:
            return "No matching Home Assistant entities."
        now = self._now()
        parts = []
        shown = available[:_MAX_LIST_RESULTS]
        if shown:
            parts.append("; ".join(_entity_line(entity, now) for entity in shown))
            if len(available) > len(shown):
                parts.append(
                    f"showing {len(shown)} of {len(available)} available — "
                    "use a domain or query filter"
                )
        else:
            parts.append("No available Home Assistant entities match")
        if stale:
            listed = stale[:_MAX_STALE_RESULTS]
            summary = "; ".join(_entity_line(entity, now) for entity in listed)
            if len(stale) > len(listed):
                summary += f", +{len(stale) - len(listed)} more"
            parts.append(f"unavailable or unknown ({len(stale)}): {summary}")
        return "; ".join(parts)

    def _call(self, arguments: dict) -> str:
        entity, error = self._resolve(str(arguments.get("entity", "")))
        if error:
            return error
        service = str(arguments.get("service", "")).strip().lower()
        if "." in service:
            service_domain, service = service.split(".", 1)
            if service_domain != entity.domain:
                return (
                    f"Service domain '{service_domain}' does not match {entity.entity_id} "
                    f"({entity.domain})."
                )
        if not _SERVICE_RE.fullmatch(service):
            return "A valid Home Assistant service name is required."
        raw_data = arguments.get("data") or {}
        if not isinstance(raw_data, dict):
            return "Home Assistant service data must be an object."
        data = dict(raw_data)
        data["entity_id"] = entity.entity_id
        return self._call_and_verify(entity, service, data)

    def _call_and_verify(self, entity: HomeAssistantEntity, service: str, data: dict) -> str:
        action = f"{entity.domain}.{service}"
        # Toggle is the one service whose target state only exists relative to where the
        # entity started, so it needs a pre-action read.
        pre = None
        if service == "toggle":
            try:
                pre = self._client.get_entity(entity.entity_id)
            except HomeAssistantError:
                pre = None
        expected = _expected_effect(service, data, pre)
        result = _result(entity, action, expected or {})
        try:
            self._client.call_service(entity.domain, service, data)
        except HomeAssistantError as exc:
            return result(VerificationStatus.FAILED, reason=str(exc)).message()
        if expected is None:
            return self._report_unknown_effect(entity, action, result)
        outcome = poll_until(
            lambda: self._client.get_entity(entity.entity_id),
            lambda current: compare_fields(expected, entity_snapshot(current), {}) == ([], []),
            timeout=self._poll_timeout,
            interval=self._poll_interval,
            clock=self._clock,
            sleep=self._sleep,
        )
        if outcome.matched:
            current = outcome.value
            return result(
                VerificationStatus.CONFIRMED,
                observed=_observed(current, expected),
                summary=_entity_line(current),
            ).message()
        if outcome.value is None:
            return result(
                VerificationStatus.UNCONFIRMED,
                reason=f"the state could not be read back ({outcome.error})",
            ).message()
        current = outcome.value
        _mismatched, missing = compare_fields(expected, entity_snapshot(current), {})
        if missing:
            return result(
                VerificationStatus.UNCONFIRMED,
                observed=_observed(current, expected),
                reason=f"{entity.entity_id} does not report {', '.join(missing)}",
            ).message()
        return result(
            VerificationStatus.MISMATCH, observed=_observed(current, expected)
        ).message()

    def _report_unknown_effect(self, entity: HomeAssistantEntity, action: str, result) -> str:
        """The call went out, but Richard has no semantics for this service — so the live
        state is worth reporting while the outcome stays explicitly unverified."""
        try:
            current = self._client.get_entity(entity.entity_id)
        except HomeAssistantError as exc:
            return result(
                VerificationStatus.UNCONFIRMED,
                reason=f"the state could not be read back ({exc})",
            ).message()
        return result(
            VerificationStatus.UNCONFIRMED,
            observed={"state": current.state},
            reason=f"Richard cannot infer the state {action} should produce",
        ).message()

    def _resolve(self, target: str) -> tuple[HomeAssistantEntity | None, str | None]:
        needle = target.strip().lower()
        if not needle:
            return None, "A Home Assistant entity id or friendly name is required."
        entities = self._client.list_entities()
        exact = [
            entity
            for entity in entities
            if entity.entity_id.lower() == needle or entity.name.lower() == needle
        ]
        if len(exact) == 1:
            return exact[0], None
        matches = [
            entity
            for entity in entities
            if needle in entity.entity_id.lower() or needle in entity.name.lower()
        ]
        if not matches:
            return None, f"I don't see a Home Assistant entity matching '{target}'."
        if len(matches) > 1:
            choices = ", ".join(f"{entity.name} ({entity.entity_id})" for entity in matches[:8])
            return None, f"That matches more than one Home Assistant entity: {choices}."
        return matches[0], None
