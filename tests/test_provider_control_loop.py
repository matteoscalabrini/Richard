import json
from datetime import datetime, timedelta, timezone

from richard.control_loops import ControlLoopStore, ControlTargetReader
from richard.plugins.home_assistant.client import HomeAssistantEntity
from richard.plugins.home_assistant.reader import HomeAssistantTargetReader
from richard.providers.control_loop import ControlLoopProvider


class FakeHomeAssistant:
    def __init__(self, entities):
        self.entities = {entity.entity_id: entity for entity in entities}

    def list_entities(self):
        return list(self.entities.values())

    def get_entity(self, entity_id):
        return self.entities[entity_id]


def _provider():
    ha = FakeHomeAssistant(
        [HomeAssistantEntity("light.desk", "off", {"friendly_name": "Desk Lamp"})]
    )
    store = ControlLoopStore(":memory:")
    reader = ControlTargetReader(readers={"ha": HomeAssistantTargetReader(ha)})
    return ControlLoopProvider(store, reader), store


def _scheduled_provider(now):
    ha = FakeHomeAssistant(
        [HomeAssistantEntity("light.lamp", "off", {"friendly_name": "Lamp"})]
    )
    store = ControlLoopStore(":memory:", now=now)
    reader = ControlTargetReader(readers={"ha": HomeAssistantTargetReader(ha)})
    return ControlLoopProvider(store, reader, now=now), store


_TOOL_NOW = datetime(2026, 7, 16, 12, 0, 0, tzinfo=timezone.utc)


def test_provider_exposes_crud_tools():
    provider, _store = _provider()
    names = [schema["function"]["name"] for schema in provider.schemas()]
    assert names == [
        "create_control_loop",
        "list_control_loops",
        "update_control_loop",
        "delete_control_loop",
    ]


def test_provider_creates_describes_pauses_and_deletes_loop():
    provider, store = _provider()
    result = provider.execute(
        "create_control_loop",
        {
            "name": "Desk light watch",
            "targets": ["Desk Lamp"],
            "trigger_description": "When the lamp turns on after midnight, turn it off.",
            "interval_seconds": 10,
        },
    )
    assert "Created control loop" in result
    loop = store.all()[0]
    assert loop.targets == ("ha:light.desk",)
    assert "after midnight" in loop.trigger_description
    assert "Desk light watch" in provider.execute("list_control_loops", {})
    assert "paused" in provider.execute(
        "update_control_loop", {"loop_id": loop.id, "enabled": False}
    )
    assert "Deleted" in provider.execute("delete_control_loop", {"loop_id": loop.id})
    assert store.all() == []


def test_provider_refuses_unknown_target():
    provider, store = _provider()
    result = provider.execute(
        "create_control_loop",
        {"name": "Nope", "targets": ["Moon lamp"], "trigger_description": "Any change"},
    )
    assert "don't see" in result
    assert store.all() == []


def test_create_scheduled_loop_with_minutes_sugar():
    provider, store = _scheduled_provider(now=lambda: _TOOL_NOW)
    result = provider.execute(
        "create_control_loop",
        {
            "name": "Door recheck",
            "trigger_description": "Is the front door still open? If yes, notify.",
            "schedule": {"once_in_minutes": 10},
        },
    )
    assert "Door recheck" in result
    loop = store.all()[0]
    assert loop.kind == "scheduled"
    assert loop.schedule == {"once_at": (_TOOL_NOW + timedelta(minutes=10)).isoformat()}
    assert loop.targets == ()


def test_create_scheduled_loop_every_minutes_compiles_to_seconds():
    provider, store = _scheduled_provider(now=lambda: _TOOL_NOW)
    provider.execute(
        "create_control_loop",
        {
            "name": "Hourly sweep",
            "trigger_description": "t",
            "targets": ["ha:light.lamp"],
            "schedule": {"every_minutes": 60},
        },
    )
    assert store.all()[0].schedule == {"every_seconds": 3600.0}


def test_listing_renders_schedule_and_next_run():
    provider, store = _scheduled_provider(now=lambda: _TOOL_NOW)
    provider.execute(
        "create_control_loop",
        {
            "name": "Morning",
            "trigger_description": "t",
            "schedule": {"at": "08:00"},
        },
    )
    listing = provider.execute("list_control_loops", {})
    assert "daily at 08:00" in listing
    assert "next 2026-07-17" in listing


def test_schemas_teach_the_one_shot_duration_pattern():
    provider, _ = _scheduled_provider(now=lambda: _TOOL_NOW)
    create = next(
        s for s in provider.schemas() if s["function"]["name"] == "create_control_loop"
    )
    description = json.dumps(create)
    assert "once_in_minutes" in description
    assert "one-shot" in description


def test_invalid_schedule_is_reported_not_raised():
    provider, store = _scheduled_provider(now=lambda: _TOOL_NOW)
    result = provider.execute(
        "create_control_loop",
        {"name": "x", "trigger_description": "t", "schedule": {"at": "8am"}},
    )
    assert "HH:MM" in result
    assert store.all() == []


def test_empty_schedule_dict_reports_validation_error_not_silent_change_loop():
    provider, store = _scheduled_provider(now=lambda: _TOOL_NOW)
    result = provider.execute(
        "create_control_loop",
        {"name": "x", "trigger_description": "t", "schedule": {}},
    )
    assert "exactly one" in result
    assert store.all() == []


def test_create_scheduled_loop_with_unresolvable_target_is_reported_not_raised():
    provider, store = _scheduled_provider(now=lambda: _TOOL_NOW)
    result = provider.execute(
        "create_control_loop",
        {
            "name": "Ghost check",
            "trigger_description": "t",
            "targets": ["ghost lamp"],
            "schedule": {"every_minutes": 60},
        },
    )
    assert "don't see" in result
    assert store.all() == []
