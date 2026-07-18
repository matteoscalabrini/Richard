from datetime import datetime, timezone

from richard.control_loops import (
    ControlLoopMonitor,
    ControlLoopStore,
    ControlTargetReader,
    describe_changes,
)
from richard.home_assistant import HomeAssistantEntity


class FakeHomeAssistant:
    def __init__(self):
        self.entities = {
            "light.workbench": HomeAssistantEntity(
                "light.workbench",
                "off",
                {"friendly_name": "Workbench Lamp", "brightness": 0},
            ),
            "sensor.temperature": HomeAssistantEntity(
                "sensor.temperature",
                "20",
                {"friendly_name": "Kitchen Temperature", "unit_of_measurement": "°C"},
            ),
        }

    def list_entities(self):
        return list(self.entities.values())

    def get_entity(self, entity_id):
        try:
            return self.entities[entity_id]
        except KeyError:
            raise RuntimeError(f"unknown entity: {entity_id}") from None


def _reader():
    ha = FakeHomeAssistant()
    return ControlTargetReader(home_assistant=ha), ha


def test_store_create_update_pause_resume_and_delete():
    store = ControlLoopStore(":memory:")
    loop = store.create(
        name="Workshop safety",
        targets=["ha:light.workbench", "ha:sensor.temperature"],
        trigger_description="Tell me if the lamp is on while temperature exceeds 30 C.",
        interval_seconds=15,
    )
    assert loop.targets == ("ha:light.workbench", "ha:sensor.temperature")
    assert loop.enabled is True
    assert store.update(loop.id, enabled=False).enabled is False
    resumed = store.update(loop.id, enabled=True, interval_seconds=20)
    assert resumed.enabled is True
    assert resumed.interval_seconds == 20
    assert resumed.last_snapshot is None
    assert store.remove(loop.id) is True
    assert store.get(loop.id) is None


def test_target_reader_resolves_names_and_entity_ids():
    reader, _ha = _reader()
    targets, error = reader.resolve(["Workbench Lamp", "sensor.temperature"])
    assert error is None
    assert targets == ["ha:light.workbench", "ha:sensor.temperature"]
    assert reader.read("ha:light.workbench")["attributes"]["brightness"] == 0
    assert reader.read("ha:sensor.temperature")["state"] == "20"


def test_target_reader_reports_ambiguous_name():
    reader, ha = _reader()
    ha.entities["light.workbench_2"] = HomeAssistantEntity(
        "light.workbench_2", "off", {"friendly_name": "Workbench Lamp"}
    )
    targets, error = reader.resolve(["Workbench Lamp"])
    assert targets == []
    assert "more than one" in error
    assert "light.workbench" in error and "light.workbench_2" in error


def test_monitor_baselines_then_notifies_llm_once_for_multi_target_change():
    reader, ha = _reader()
    store = ControlLoopStore(":memory:")
    loop = store.create(
        name="Kitchen watch",
        targets=["ha:light.workbench", "ha:sensor.temperature"],
        trigger_description="Alert if either becomes unsafe.",
        interval_seconds=5,
    )
    prompts = []

    def notify(change):
        prompts.append(change.llm_prompt())
        return "The lamp switched on and the room warmed up."

    monitor = ControlLoopMonitor(store, reader, notify)
    assert monitor.check_once(force=True) == []  # first read is only the baseline
    assert store.get(loop.id).last_snapshot["ha:light.workbench"]["state"] == "off"

    ha.entities["light.workbench"] = HomeAssistantEntity(
        "light.workbench", "on", {"friendly_name": "Workbench Lamp", "brightness": 80}
    )
    ha.entities["sensor.temperature"] = HomeAssistantEntity(
        "sensor.temperature",
        "31",
        {"friendly_name": "Kitchen Temperature", "unit_of_measurement": "°C"},
    )
    changes = monitor.check_once(force=True)
    assert len(changes) == 1
    assert "Workbench Lamp" in changes[0].summary
    assert "Kitchen Temperature" in changes[0].summary
    assert "Alert if either becomes unsafe" in prompts[0]
    notifications = store.notifications()
    assert notifications[0].response == "The lamp switched on and the room warmed up."

    monitor.check_once(force=True)
    assert len(store.notifications()) == 1  # unchanged snapshots do not retrigger


def test_monitor_records_read_errors_without_overwriting_baseline():
    reader, ha = _reader()
    store = ControlLoopStore(":memory:")
    loop = store.create(
        name="Lamp",
        targets=["ha:light.workbench"],
        trigger_description="Report changes.",
        interval_seconds=5,
    )
    monitor = ControlLoopMonitor(store, reader, lambda change: "done")
    monitor.check_once(force=True)
    baseline = store.get(loop.id).last_snapshot
    del ha.entities["light.workbench"]
    assert monitor.check_once(force=True) == []
    current = store.get(loop.id)
    assert "light.workbench" in current.last_error
    assert current.last_snapshot == baseline


def test_monitor_suppresses_inbox_item_when_llm_says_trigger_did_not_match():
    reader, ha = _reader()
    store = ControlLoopStore(":memory:")
    store.create(
        name="Only bright",
        targets=["ha:light.workbench"],
        trigger_description="Notify only above 90 percent brightness.",
        interval_seconds=5,
    )
    monitor = ControlLoopMonitor(
        store, reader, lambda change: "CONTROL_LOOP_NO_TRIGGER"
    )
    monitor.check_once(force=True)
    ha.entities["light.workbench"] = HomeAssistantEntity(
        "light.workbench", "on", {"friendly_name": "Workbench Lamp", "brightness": 20}
    )
    assert len(monitor.check_once(force=True)) == 1
    assert store.notifications() == []


def test_describe_changes_reports_state_and_attribute_deltas():
    before = {"ha:light.one": {"name": "Lamp", "state": "off", "attributes": {"brightness": 0}}}
    after = {"ha:light.one": {"name": "Lamp", "state": "on", "attributes": {"brightness": 100}}}
    summary = describe_changes(before, after)
    assert 'state: "off" -> "on"' in summary
    assert "brightness: 0 -> 100" in summary


_SCHED_NOW = datetime(2026, 7, 16, 12, 0, 0, tzinfo=timezone.utc)


def _scheduled_store():
    from richard.control_loops import ControlLoopStore

    return ControlLoopStore(":memory:", now=lambda: _SCHED_NOW)


def test_scheduled_loop_roundtrips_kind_schedule_and_next_run():
    store = _scheduled_store()
    loop = store.create(
        name="Morning check",
        targets=["ha:binary_sensor.front_door"],
        trigger_description="Report anything left open.",
        kind="scheduled",
        schedule={"at": "08:00"},
    )
    assert loop.kind == "scheduled"
    assert loop.schedule == {"at": "08:00"}
    # next 08:00 local strictly after _SCHED_NOW (12:00Z) is tomorrow, stored as UTC ISO.
    assert loop.next_run_at is not None
    assert datetime.fromisoformat(loop.next_run_at) > _SCHED_NOW
    assert store.get(loop.id).schedule == {"at": "08:00"}


def test_scheduled_loop_allows_zero_targets_change_still_requires_one():
    store = _scheduled_store()
    reminder = store.create(
        name="Water plants",
        targets=[],
        trigger_description="Remind me to water the plants.",
        kind="scheduled",
        schedule={"once_at": "2026-07-16T18:30:00+00:00"},
    )
    assert reminder.targets == ()
    import pytest

    with pytest.raises(ValueError, match="at least one"):
        store.create(name="x", targets=[], trigger_description="t")


def test_change_loop_rejects_schedule_and_scheduled_requires_one():
    import pytest

    store = _scheduled_store()
    with pytest.raises(ValueError, match="cannot have a schedule"):
        store.create(
            name="x",
            targets=["ha:light.lamp"],
            trigger_description="t",
            schedule={"at": "08:00"},
        )
    with pytest.raises(ValueError, match="requires a schedule"):
        store.create(name="x", targets=["ha:light.lamp"], trigger_description="t", kind="scheduled")


def test_record_scheduled_fire_advances_or_disables():
    store = _scheduled_store()
    loop = store.create(
        name="Sweep",
        targets=[],
        trigger_description="t",
        kind="scheduled",
        schedule={"every_seconds": 600},
    )
    store.record_scheduled_fire(loop.id, {}, next_run_at="2026-07-16T12:10:00+00:00")
    advanced = store.get(loop.id)
    assert advanced.next_run_at == "2026-07-16T12:10:00+00:00"
    assert advanced.enabled

    store.record_scheduled_fire(loop.id, {}, next_run_at=None)
    fired = store.get(loop.id)
    assert fired.next_run_at is None
    assert not fired.enabled


def test_update_schedule_recomputes_next_run_and_kind_is_immutable():
    import pytest

    store = _scheduled_store()
    loop = store.create(
        name="Sweep",
        targets=[],
        trigger_description="t",
        kind="scheduled",
        schedule={"every_seconds": 600},
    )
    updated = store.update(loop.id, schedule={"every_seconds": 1200})
    assert updated.schedule == {"every_seconds": 1200.0}
    assert datetime.fromisoformat(updated.next_run_at) == _SCHED_NOW.replace(minute=20)

    change_loop = store.create(name="c", targets=["ha:light.lamp"], trigger_description="t")
    with pytest.raises(ValueError, match="scheduled"):
        store.update(change_loop.id, schedule={"at": "08:00"})


def test_reenabling_a_scheduled_loop_recomputes_next_run():
    store = _scheduled_store()
    loop = store.create(
        name="Sweep",
        targets=[],
        trigger_description="t",
        kind="scheduled",
        schedule={"every_seconds": 600},
    )
    store.record_scheduled_fire(loop.id, {}, next_run_at=None)  # one-shot-style disable
    revived = store.update(loop.id, enabled=True)
    assert revived.enabled
    assert datetime.fromisoformat(revived.next_run_at) == _SCHED_NOW.replace(minute=10)


def test_create_coerces_non_string_targets():
    store = _scheduled_store()
    loop = store.create(
        name="x",
        targets=[123],
        trigger_description="t",
        kind="scheduled",
        schedule={"every_seconds": 600},
    )
    assert loop.targets == ("123",)


def test_existing_database_gains_new_columns(tmp_path):
    import sqlite3

    from richard.control_loops import ControlLoopStore

    path = tmp_path / "loops.db"
    conn = sqlite3.connect(path)
    conn.executescript(
        """
        CREATE TABLE control_loops (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            targets TEXT NOT NULL,
            trigger_description TEXT NOT NULL,
            interval_seconds REAL NOT NULL,
            enabled INTEGER NOT NULL DEFAULT 1,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            last_checked_at TEXT,
            last_changed_at TEXT,
            last_snapshot TEXT,
            last_error TEXT
        );
        INSERT INTO control_loops
            (name, targets, trigger_description, interval_seconds, created_at, updated_at)
        VALUES ('legacy', '["ha:light.lamp"]', 't', 30.0, 'x', 'x');
        """
    )
    conn.commit()
    conn.close()

    store = ControlLoopStore(path, now=lambda: _SCHED_NOW)
    legacy = store.all()[0]
    assert legacy.kind == "change"
    assert legacy.schedule is None
    assert legacy.next_run_at is None


class _StubReader:
    def __init__(self, snapshots=None, errors=None):
        self.snapshots = snapshots or {}
        self.errors = errors or {}
        self.reads = []

    def read(self, target):
        self.reads.append(target)
        if target in self.errors:
            raise RuntimeError(self.errors[target])
        return self.snapshots[target]


def _scheduled_monitor(store, reader=None, responses=None, now=None):
    from richard.control_loops import ControlLoopMonitor

    calls = []

    def on_change(event):
        calls.append(event)
        if responses is None:
            return "CONTROL_LOOP_NO_TRIGGER"
        result = responses.pop(0)
        if isinstance(result, Exception):
            raise result
        return result

    clock = {"now": now or _SCHED_NOW}
    monitor = ControlLoopMonitor(
        store,
        reader or _StubReader(),
        on_change,
        now=lambda: clock["now"],
    )
    return monitor, calls, clock


def test_scheduled_loop_fires_when_due_and_not_before():
    store = _scheduled_store()
    store.create(
        name="Sweep",
        targets=[],
        trigger_description="Check the house.",
        kind="scheduled",
        schedule={"every_seconds": 600},
    )
    monitor, calls, clock = _scheduled_monitor(store)
    assert monitor.check_once() == []
    assert calls == []

    from datetime import timedelta

    clock["now"] = _SCHED_NOW + timedelta(seconds=601)
    fired = monitor.check_once()
    assert len(fired) == 1
    assert len(calls) == 1
    prompt = calls[0].llm_prompt()
    assert prompt.startswith("[SCHEDULED CONTROL LOOP CHECK]")
    assert "Check the house." in prompt
    assert "no targets are attached" in prompt
    assert "CONTROL_LOOP_NO_TRIGGER" in prompt


def test_scheduled_fire_persists_next_run_before_llm_and_no_trigger_is_silent():
    store = _scheduled_store()
    loop = store.create(
        name="Sweep",
        targets=[],
        trigger_description="t",
        kind="scheduled",
        schedule={"every_seconds": 600},
    )
    seen_next = {}

    def on_change(event):
        seen_next["value"] = store.get(loop.id).next_run_at
        return "CONTROL_LOOP_NO_TRIGGER"

    from datetime import timedelta
    from richard.control_loops import ControlLoopMonitor

    later = _SCHED_NOW + timedelta(seconds=700)
    monitor = ControlLoopMonitor(store, _StubReader(), on_change, now=lambda: later)
    monitor.check_once()
    # next_run_at was already advanced (from the fire wall-clock) when the LLM ran.
    assert seen_next["value"] == (later + timedelta(seconds=600)).isoformat()
    assert store.notifications() == []


def test_one_shot_fires_once_disables_and_notifies():
    store = _scheduled_store()
    store.create(
        name="Door recheck",
        targets=[],
        trigger_description="Is the door still open?",
        kind="scheduled",
        schedule={"once_at": "2026-07-16T12:10:00+00:00"},
    )
    from datetime import timedelta

    monitor, calls, clock = _scheduled_monitor(store, responses=["Door is still open."])
    clock["now"] = _SCHED_NOW + timedelta(minutes=11)
    assert len(monitor.check_once()) == 1
    remaining = store.all()[0]
    assert not remaining.enabled
    assert remaining.next_run_at is None
    notes = store.notifications()
    assert len(notes) == 1
    assert notes[0].response == "Door is still open."

    assert monitor.check_once() == []  # disabled: never fires again
    assert len(calls) == 1


def test_missed_schedule_fires_once_on_catch_up():
    store = _scheduled_store()
    store.create(
        name="Morning",
        targets=[],
        trigger_description="t",
        kind="scheduled",
        schedule={"every_seconds": 600},
    )
    from datetime import timedelta

    monitor, calls, clock = _scheduled_monitor(store)
    clock["now"] = _SCHED_NOW + timedelta(hours=6)  # many occurrences missed
    assert len(monitor.check_once()) == 1
    assert monitor.check_once() == []  # rescheduled from now, not from the backlog
    assert store.all()[0].next_run_at == (clock["now"] + timedelta(seconds=600)).isoformat()


def test_scheduled_wake_proceeds_with_unreadable_targets():
    store = _scheduled_store()
    store.create(
        name="Sweep",
        targets=["ha:sensor.dead", "ha:light.lamp"],
        trigger_description="t",
        kind="scheduled",
        schedule={"every_seconds": 600},
    )
    reader = _StubReader(
        snapshots={"ha:light.lamp": {"name": "Lamp", "state": {"on": True}, "attributes": {}}},
        errors={"ha:sensor.dead": "Home Assistant is unreachable"},
    )
    from datetime import timedelta

    monitor, calls, clock = _scheduled_monitor(store, reader=reader)
    clock["now"] = _SCHED_NOW + timedelta(seconds=601)
    monitor.check_once()
    prompt = calls[0].llm_prompt()
    assert "ha:sensor.dead: could not be read (Home Assistant is unreachable)" in prompt
    assert "Lamp (ha:light.lamp)" in prompt


def test_scheduled_llm_failure_writes_error_notification():
    store = _scheduled_store()
    store.create(
        name="Sweep",
        targets=[],
        trigger_description="t",
        kind="scheduled",
        schedule={"every_seconds": 600},
    )
    from datetime import timedelta

    monitor, calls, clock = _scheduled_monitor(store, responses=[RuntimeError("brain down")])
    clock["now"] = _SCHED_NOW + timedelta(seconds=601)
    monitor.check_once()
    notes = store.notifications()
    assert len(notes) == 1
    assert notes[0].error == "brain down"
    assert monitor.check_once() == []  # occurrence not replayed after the failure
