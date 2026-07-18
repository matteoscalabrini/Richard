from datetime import datetime, timezone

import pytest

from richard.errors import HomeAssistantError
from richard.home_assistant import HomeAssistantEntity
from richard.providers.home_assistant import HomeAssistantProvider

_NOW = datetime(2026, 7, 16, 12, 0, 0, tzinfo=timezone.utc)


class FakeClient:
    """A stateful stand-in for Home Assistant: a service call actually changes the state
    a later read reports, so read-back verification is exercised for real."""

    def __init__(self):
        self.entities = [
            HomeAssistantEntity(
                "light.kitchen",
                "off",
                {"friendly_name": "Kitchen Light", "brightness": 0},
            ),
            HomeAssistantEntity(
                "sensor.kitchen_temperature",
                "21.5",
                {"friendly_name": "Kitchen Temperature", "unit_of_measurement": "°C"},
            ),
            HomeAssistantEntity(
                "light.bedroom",
                "on",
                {"friendly_name": "Bedroom Light", "brightness": 180},
            ),
        ]
        self.calls = []
        # How many reads keep returning the old state before the change shows up,
        # modelling Home Assistant's asynchronous transitions.
        self.settle_reads = 0
        self._stale = None

    def _index(self, entity_id):
        for index, entity in enumerate(self.entities):
            if entity.entity_id == entity_id:
                return index
        raise AssertionError(entity_id)

    def list_entities(self):
        return list(self.entities)

    def get_entity(self, entity_id):
        self.calls.append(("get_entity", entity_id))
        if self.settle_reads > 0 and self._stale is not None:
            self.settle_reads -= 1
            return self._stale
        return self.entities[self._index(entity_id)]

    def _apply(self, entity_id, state=None, attributes=None):
        index = self._index(entity_id)
        current = self.entities[index]
        merged = dict(current.attributes)
        merged.update(attributes or {})
        self.entities[index] = HomeAssistantEntity(
            entity_id, state or current.state, merged
        )

    def call_service(self, domain, service, data):
        self.calls.append(("call_service", domain, service, data))
        entity_id = data["entity_id"]
        if self.settle_reads:
            self._stale = self.entities[self._index(entity_id)]
        if service == "turn_on":
            attributes = {}
            if "brightness_pct" in data:
                attributes["brightness"] = round(255 * int(data["brightness_pct"]) / 100)
            self._apply(entity_id, "on", attributes)
        elif service == "turn_off":
            self._apply(entity_id, "off")
        elif service == "toggle":
            current = self.entities[self._index(entity_id)]
            self._apply(entity_id, "off" if current.state == "on" else "on")
        elif service == "set_percentage":
            self._apply(entity_id, "on", {"percentage": data["percentage"]})
        elif service == "set_temperature":
            self._apply(entity_id, None, {"temperature": data["temperature"]})
        return []


def _provider(client=None, **kwargs):
    """A provider whose polling never spends real time."""
    clock = {"now": 0.0}

    def sleep(seconds):
        clock["now"] += seconds

    return HomeAssistantProvider(
        client or FakeClient(),
        clock=lambda: clock["now"],
        sleep=sleep,
        **kwargs,
    )


def test_schemas_expose_discovery_state_and_control_tools():
    names = [
        schema["function"]["name"]
        for schema in HomeAssistantProvider(FakeClient()).schemas()
    ]
    assert names == [
        "list_home_assistant_entities",
        "get_home_assistant_state",
        "call_home_assistant_service",
    ]


def test_context_tells_model_to_discover_entities():
    context = HomeAssistantProvider(FakeClient()).context()
    assert "entities" in context
    assert "Do not invent" in context


def test_list_filters_by_domain_and_query():
    provider = HomeAssistantProvider(FakeClient())
    result = provider.execute(
        "list_home_assistant_entities", {"domain": "light", "query": "kitchen"}
    )
    assert "Kitchen Light (light.kitchen): off" in result
    assert "Bedroom" not in result
    assert "Temperature" not in result


def test_context_explains_that_unavailable_sensors_are_often_just_asleep():
    context = HomeAssistantProvider(FakeClient()).context()
    assert "unavailable" in context
    assert "battery" in context
    assert "broken" in context


def _door_sensor(state="unavailable", last_changed="2026-07-14T12:00:00+00:00"):
    return HomeAssistantEntity(
        "binary_sensor.front_door",
        state,
        {"friendly_name": "Front Door"},
        last_changed=last_changed,
    )


def test_unavailable_state_is_rendered_with_its_age():
    client = FakeClient()
    client.entities.append(_door_sensor())  # went unavailable 2 days before _NOW
    provider = _provider(client, now=lambda: _NOW)
    result = provider.execute("get_home_assistant_state", {"entity": "front door"})
    assert "unavailable (since 2d ago)" in result


def test_recent_unavailability_is_rendered_in_minutes():
    client = FakeClient()
    client.entities.append(_door_sensor(last_changed="2026-07-16T11:35:00+00:00"))
    provider = _provider(client, now=lambda: _NOW)
    result = provider.execute("get_home_assistant_state", {"entity": "front door"})
    assert "unavailable (since 25m ago)" in result


def test_unavailable_without_timestamp_is_rendered_plain():
    client = FakeClient()
    client.entities.append(_door_sensor(last_changed=None))
    provider = _provider(client, now=lambda: _NOW)
    result = provider.execute("get_home_assistant_state", {"entity": "front door"})
    assert "unavailable" in result
    assert "since" not in result


def test_list_summarizes_unavailable_entities_after_the_available_ones():
    client = FakeClient()
    client.entities.append(_door_sensor())
    client.entities.append(
        HomeAssistantEntity("sensor.orphan", "unknown", {"friendly_name": "Orphan"})
    )
    provider = _provider(client, now=lambda: _NOW)
    result = provider.execute("list_home_assistant_entities", {})
    available_part, _, stale_part = result.partition("unavailable or unknown (2): ")
    assert "Kitchen Light (light.kitchen): off" in available_part
    assert "front_door" not in available_part
    assert "Front Door (binary_sensor.front_door): unavailable (since 2d ago)" in stale_part
    assert "Orphan (sensor.orphan): unknown" in stale_part


def test_list_matching_only_unavailable_entities_still_reports_them():
    client = FakeClient()
    client.entities.append(_door_sensor())
    provider = _provider(client, now=lambda: _NOW)
    result = provider.execute("list_home_assistant_entities", {"query": "door"})
    assert "No matching" not in result
    assert "unavailable or unknown (1): " in result
    assert "Front Door (binary_sensor.front_door): unavailable (since 2d ago)" in result


def test_get_state_resolves_friendly_name_and_includes_attributes():
    provider = HomeAssistantProvider(FakeClient())
    result = provider.execute(
        "get_home_assistant_state", {"entity": "Kitchen Temperature"}
    )
    assert "sensor.kitchen_temperature" in result
    assert "21.5°C" in result
    assert "unit_of_measurement" in result


def test_ambiguous_target_is_reported_without_action():
    client = FakeClient()
    result = _provider(client).execute(
        "call_home_assistant_service", {"entity": "kitchen", "service": "turn_on"}
    )
    assert "more than one" in result
    assert client.calls == []


def test_call_service_uses_entity_domain_forces_id_and_verifies_state():
    client = FakeClient()
    result = _provider(client).execute(
        "call_home_assistant_service",
        {
            "entity": "light.kitchen",
            "service": "light.turn_on",
            "data": {"entity_id": "light.somewhere_else", "brightness_pct": 40},
        },
    )
    assert client.calls[0] == (
        "call_service",
        "light",
        "turn_on",
        {"entity_id": "light.kitchen", "brightness_pct": 40},
    )
    assert client.calls[1] == ("get_entity", "light.kitchen")
    assert result.startswith("CONFIRMED: ")


def test_service_domain_must_match_entity():
    client = FakeClient()
    result = _provider(client).execute(
        "call_home_assistant_service",
        {"entity": "light.kitchen", "service": "switch.turn_on"},
    )
    assert "does not match" in result
    assert client.calls == []


# --- verification: same four outcomes as the native path ---


@pytest.mark.parametrize(
    "service,expected_state",
    [("turn_on", "on"), ("turn_off", "off")],
)
def test_turn_on_and_off_are_confirmed_against_a_read_back(service, expected_state):
    client = FakeClient()
    result = _provider(client).execute(
        "call_home_assistant_service", {"entity": "light.bedroom", "service": service}
    )
    assert result.startswith("CONFIRMED: ")
    assert client.entities[2].state == expected_state
    assert ("get_entity", "light.bedroom") in client.calls


def test_toggle_expectation_is_derived_from_the_pre_action_state():
    client = FakeClient()
    # Bedroom starts on, so a toggle must be verified against "off".
    result = _provider(client).execute(
        "call_home_assistant_service", {"entity": "light.bedroom", "service": "toggle"}
    )
    assert result.startswith("CONFIRMED: ")
    assert client.calls[0] == ("get_entity", "light.bedroom")  # pre-state read first
    assert client.entities[2].state == "off"


def test_toggle_without_a_known_on_off_pre_state_is_unconfirmed():
    client = FakeClient()
    client.entities[1] = HomeAssistantEntity(
        "sensor.kitchen_temperature", "unavailable", {"friendly_name": "Kitchen Temperature"}
    )
    result = _provider(client).execute(
        "call_home_assistant_service",
        {"entity": "sensor.kitchen_temperature", "service": "toggle"},
    )
    assert result.startswith("UNCONFIRMED: ")
    assert "Do not claim success." in result


def test_delayed_transition_is_confirmed_by_polling():
    client = FakeClient()
    client.settle_reads = 2  # the first two reads still show the old state
    result = _provider(client).execute(
        "call_home_assistant_service", {"entity": "light.kitchen", "service": "turn_on"}
    )
    assert result.startswith("CONFIRMED: ")
    assert len([c for c in client.calls if c[0] == "get_entity"]) == 3


def test_state_that_never_arrives_is_a_mismatch_not_a_success():
    client = FakeClient()
    client.settle_reads = 999  # the change never becomes visible
    result = _provider(client).execute(
        "call_home_assistant_service", {"entity": "light.kitchen", "service": "turn_on"}
    )
    assert result.startswith("MISMATCH: ")
    assert "Do not claim success." in result


def test_brightness_pct_is_verified_against_home_assistants_0_255_scale():
    client = FakeClient()
    result = _provider(client).execute(
        "call_home_assistant_service",
        {"entity": "light.kitchen", "service": "turn_on", "data": {"brightness_pct": 40}},
    )
    assert result.startswith("CONFIRMED: ")
    assert client.entities[0].attributes["brightness"] == 102  # 40% of 255


def test_brightness_pct_mismatch_is_reported():
    class CappedClient(FakeClient):
        def call_service(self, domain, service, data):
            self.calls.append(("call_service", domain, service, data))
            self._apply(data["entity_id"], "on", {"brightness": 255})  # ignores the pct
            return []

    result = _provider(CappedClient()).execute(
        "call_home_assistant_service",
        {"entity": "light.kitchen", "service": "turn_on", "data": {"brightness_pct": 40}},
    )
    assert result.startswith("MISMATCH: ")


@pytest.mark.parametrize(
    "entity,service,data,attribute,value",
    [
        ("light.kitchen", "set_percentage", {"percentage": 60}, "percentage", 60),
        ("light.kitchen", "set_temperature", {"temperature": 21}, "temperature", 21),
    ],
)
def test_supported_attribute_services_are_verified(entity, service, data, attribute, value):
    client = FakeClient()
    result = _provider(client).execute(
        "call_home_assistant_service", {"entity": entity, "service": service, "data": data}
    )
    assert result.startswith("CONFIRMED: ")
    assert client.entities[0].attributes[attribute] == value


def test_unsupported_service_is_unconfirmed_and_reports_current_state():
    client = FakeClient()
    result = _provider(client).execute(
        "call_home_assistant_service",
        {"entity": "light.kitchen", "service": "start_disco_mode"},
    )
    # Richard cannot infer what "start_disco_mode" should leave behind, so it must not
    # pretend to know — but the call was made and the live state is still worth reporting.
    assert result.startswith("UNCONFIRMED: ")
    assert "Do not claim success." in result
    assert ("get_entity", "light.kitchen") in client.calls


def test_rejected_service_call_is_failed():
    class RejectingClient(FakeClient):
        def call_service(self, domain, service, data):
            raise HomeAssistantError("Home Assistant returned HTTP 400")

    result = _provider(RejectingClient()).execute(
        "call_home_assistant_service", {"entity": "light.kitchen", "service": "turn_on"}
    )
    assert result.startswith("FAILED: ")
    assert "Do not claim success." in result


def test_successful_call_followed_by_a_read_failure_is_unconfirmed():
    class BlindClient(FakeClient):
        def get_entity(self, entity_id):
            raise HomeAssistantError("Home Assistant is unreachable")

    result = _provider(BlindClient()).execute(
        "call_home_assistant_service", {"entity": "light.kitchen", "service": "turn_on"}
    )
    assert result.startswith("UNCONFIRMED: ")
    assert "Do not claim success." in result


def test_context_marks_verification_results_authoritative():
    context = _provider().context()
    assert "CONFIRMED" in context


def test_client_error_becomes_tool_result():
    class BrokenClient(FakeClient):
        def list_entities(self):
            raise HomeAssistantError("Home Assistant is unreachable")

    result = HomeAssistantProvider(BrokenClient()).execute(
        "list_home_assistant_entities", {}
    )
    assert result == "Home Assistant is unreachable"
