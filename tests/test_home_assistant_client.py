import json

import httpx
import pytest

from richard.errors import HomeAssistantError
from richard.home_assistant import HomeAssistantClient


def _client(handler, base_url="http://ha.local:8123/api"):
    transport = httpx.MockTransport(handler)
    return HomeAssistantClient(
        base_url,
        "secret-token",
        client=httpx.Client(transport=transport),
    )


def test_list_entities_uses_bearer_auth_and_parses_states():
    def handler(request):
        assert request.url == "http://ha.local:8123/api/states"
        assert request.headers["Authorization"] == "Bearer secret-token"
        return httpx.Response(
            200,
            json=[
                {
                    "entity_id": "light.kitchen",
                    "state": "on",
                    "attributes": {"friendly_name": "Kitchen Light"},
                }
            ],
        )

    entities = _client(handler).list_entities()
    assert len(entities) == 1
    assert entities[0].entity_id == "light.kitchen"
    assert entities[0].domain == "light"
    assert entities[0].name == "Kitchen Light"


def test_get_entity():
    def handler(request):
        assert request.url.path == "/api/states/sensor.temperature"
        return httpx.Response(
            200,
            json={
                "entity_id": "sensor.temperature",
                "state": "21.5",
                "attributes": {"unit_of_measurement": "°C"},
            },
        )

    entity = _client(handler).get_entity("sensor.temperature")
    assert entity.state == "21.5"


def test_entity_captures_recency_timestamps():
    def handler(request):
        return httpx.Response(
            200,
            json={
                "entity_id": "binary_sensor.front_door",
                "state": "unavailable",
                "attributes": {},
                "last_changed": "2026-07-14T09:00:00+00:00",
                "last_updated": "2026-07-14T09:30:00+00:00",
            },
        )

    entity = _client(handler).get_entity("binary_sensor.front_door")
    assert entity.last_changed == "2026-07-14T09:00:00+00:00"
    assert entity.last_updated == "2026-07-14T09:30:00+00:00"


def test_entity_timestamps_default_to_none_when_absent():
    def handler(request):
        return httpx.Response(
            200,
            json={"entity_id": "light.kitchen", "state": "on", "attributes": {}},
        )

    entity = _client(handler).get_entity("light.kitchen")
    assert entity.last_changed is None
    assert entity.last_updated is None


def test_call_service_posts_json_and_returns_changed_states():
    def handler(request):
        assert request.url.path == "/api/services/light/turn_on"
        assert json.loads(request.content) == {
            "entity_id": "light.kitchen",
            "brightness_pct": 40,
        }
        return httpx.Response(
            200,
            json=[{"entity_id": "light.kitchen", "state": "on", "attributes": {}}],
        )

    changed = _client(handler).call_service(
        "light", "turn_on", {"entity_id": "light.kitchen", "brightness_pct": 40}
    )
    assert changed[0].state == "on"


def test_unauthorized_response_has_useful_error():
    client = _client(lambda request: httpx.Response(401, request=request))
    with pytest.raises(HomeAssistantError, match="rejected the access token"):
        client.list_entities()


def test_invalid_states_shape_is_rejected():
    client = _client(lambda request: httpx.Response(200, json={"not": "a list"}))
    with pytest.raises(HomeAssistantError, match="invalid states response"):
        client.list_entities()
