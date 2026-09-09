from richard.diagnostics import DiagnosticsProvider, DiagnosticsService
from richard.errors import HomeAssistantError
from richard.plugins.base import TargetInfo
from richard.plugins.home_assistant.client import HomeAssistantEntity
from richard.plugins.home_assistant.reader import HomeAssistantTargetReader


class FakeClient:
    def __init__(self, entities=None, error=None):
        self.entities = entities if entities is not None else [
            HomeAssistantEntity("light.kitchen", "on", {"friendly_name": "Kitchen Light"}),
            HomeAssistantEntity("lock.front", "locked", {"friendly_name": "Front Door"}),
        ]
        self.error = error

    def list_entities(self):
        if self.error:
            raise HomeAssistantError(self.error)
        return list(self.entities)

    def get_entity(self, entity_id):
        if self.error:
            raise HomeAssistantError(self.error)
        for entity in self.entities:
            if entity.entity_id == entity_id:
                return entity
        raise HomeAssistantError(f"{entity_id} was not found")


def _service(client=None, readers=None):
    if readers is None:
        readers = {"ha": HomeAssistantTargetReader(client or FakeClient())}
    return DiagnosticsService(readers=readers)


# --- refresh ---


def test_refresh_with_no_sources():
    report = DiagnosticsService(readers={}).refresh()
    assert report.status == "ok"
    assert report.sources == ()
    assert report.summary() == "No target sources are configured."


def test_refresh_fetches_the_live_inventory():
    report = _service().refresh()
    assert report.status == "ok"
    assert [(s.kind, s.reachable, s.target_count, s.error) for s in report.sources] == [("ha", True, 2, None)]
    assert "2 targets" in report.summary()


def test_refresh_reports_an_unreachable_source_as_partial():
    report = _service(FakeClient(error="Home Assistant is unreachable")).refresh()
    assert report.status == "partial"
    assert report.sources[0].reachable is False
    assert "unreachable" in report.sources[0].error
    assert "unreachable" in report.summary()


def test_refresh_reports_each_source():
    class Reachy:
        def read(self, target_id):
            return {"name": "face", "state": "absent", "attributes": {}}

        def list_targets(self):
            raise RuntimeError("daemon offline")

        def verify(self, target_id, expected):
            raise NotImplementedError

    report = _service(readers={"ha": HomeAssistantTargetReader(FakeClient()), "reachy": Reachy()}).refresh()
    assert report.status == "partial"
    assert [(s.kind, s.reachable, s.target_count, s.error) for s in report.sources] == [
        ("ha", True, 2, None), ("reachy", False, 0, "daemon offline"),
    ]
    assert report.summary() == "ha: 2 targets; reachy: unreachable (daemon offline)"


# --- diagnose ---


def test_diagnose_resolves_a_target_by_name():
    diagnosis = _service().diagnose("Kitchen Light")
    assert diagnosis.target == "ha:light.kitchen"
    assert diagnosis.reachable is True
    assert diagnosis.state["state"] == "on"
    message = diagnosis.message()
    assert "Kitchen Light" in message and "light.kitchen" in message


def test_diagnose_by_kind_prefix_and_by_name():
    service = _service()
    by_key = service.diagnose("ha:lock.front")
    assert by_key.reachable and by_key.target == "ha:lock.front" and by_key.state["state"] == "locked"
    by_name = service.diagnose("Kitchen Light")
    assert by_name.target == "ha:light.kitchen"


def test_diagnose_unknown_kind():
    result = _service().diagnose("reachy:face_present")
    assert not result.reachable
    assert result.error == "Unknown target source: reachy"


def test_diagnose_reports_ambiguity_without_acting():
    client = FakeClient(
        entities=[
            HomeAssistantEntity("light.kitchen", "on", {"friendly_name": "Kitchen Light"}),
            HomeAssistantEntity("switch.kitchen", "off", {"friendly_name": "Kitchen Light"}),
        ]
    )
    diagnosis = _service(client).diagnose("kitchen light")
    assert diagnosis.ambiguous
    message = diagnosis.message()
    assert "more than one" in message
    assert "ha:light.kitchen" in message and "ha:switch.kitchen" in message


def test_diagnose_reports_an_unreachable_target():
    matches = FakeClient()

    class FlakyClient(FakeClient):
        def get_entity(self, entity_id):
            raise HomeAssistantError("timed out")

    diagnosis = _service(FlakyClient(entities=matches.entities)).diagnose("Kitchen Light")
    assert diagnosis.reachable is False
    assert "timed out" in diagnosis.error
    assert "timed out" in diagnosis.message()


def test_diagnose_unknown_target_is_reported():
    diagnosis = _service().diagnose("Toaster")
    assert diagnosis.reachable is False
    assert "Toaster" in diagnosis.message()


# --- verify ---


def test_verify_checks_state_and_attributes():
    client = FakeClient(
        entities=[
            HomeAssistantEntity(
                "climate.hall", "heat", {"friendly_name": "Hall", "temperature": 21}
            )
        ]
    )
    service = _service(client)
    confirmed = service.verify("Hall", {"state": "heat", "temperature": 21})
    assert confirmed.confirmed is True
    assert confirmed.message().startswith("CONFIRMED: ")
    assert service.verify("Hall", {"temperature": 18}).message().startswith("MISMATCH: ")


def test_verify_reports_failed_when_the_target_cannot_be_read():
    class FlakyClient(FakeClient):
        def get_entity(self, entity_id):
            raise HomeAssistantError("timed out")

    result = _service(FlakyClient()).verify("Kitchen Light", {"state": "on"})
    assert result.message().startswith("FAILED: ")
    assert "timed out" in result.message()


def test_verify_reports_unconfirmed_for_a_missing_field():
    result = _service().verify("Kitchen Light", {"brightness": 80})
    assert result.message().startswith("UNCONFIRMED: ")
    assert "does not report" in result.message()


def test_verify_reports_failed_for_an_unknown_target():
    result = _service().verify("Toaster", {"state": "on"})
    assert result.message().startswith("FAILED: ")


def test_verify_delegates_to_the_reader():
    result = _service().verify("ha:light.kitchen", {"state": "on"})
    assert result.confirmed
    assert result.target == "ha:light.kitchen"


# --- provider ---


def test_schemas_expose_exactly_the_three_diagnostic_tools():
    provider = DiagnosticsProvider(_service())
    schemas = {s["function"]["name"]: s["function"] for s in provider.schemas()}
    assert list(schemas) == ["refresh_devices", "diagnose_target", "verify_target_state"]

    refresh = schemas["refresh_devices"]["parameters"]
    assert refresh.get("required", []) == []

    diagnose = schemas["diagnose_target"]["parameters"]
    assert diagnose["required"] == ["target"]

    verify = schemas["verify_target_state"]["parameters"]
    assert verify["required"] == ["target", "expected"]
    assert verify["properties"]["expected"]["type"] == "object"


def test_context_makes_the_outcomes_authoritative():
    context = DiagnosticsProvider(_service()).context()
    for token in ("CONFIRMED", "MISMATCH", "UNCONFIRMED", "FAILED"):
        assert token in context
    assert "authoritative" in context.lower()
    assert "check my devices" in context
    assert "ha:light.kitchen" in context
    assert "Home Assistant" not in context


def test_provider_refresh_returns_a_summary():
    provider = DiagnosticsProvider(_service())
    assert "2 targets" in provider.execute("refresh_devices", {})


def test_provider_diagnose_returns_the_diagnosis():
    assert "Kitchen Light" in DiagnosticsProvider(_service()).execute(
        "diagnose_target", {"target": "Kitchen Light"}
    )


def test_provider_verify_returns_a_prefixed_result():
    result = DiagnosticsProvider(_service()).execute(
        "verify_target_state", {"target": "Kitchen Light", "expected": {"state": "on"}}
    )
    assert result.startswith("CONFIRMED: ")


def test_provider_verify_requires_an_expected_object():
    result = DiagnosticsProvider(_service()).execute(
        "verify_target_state", {"target": "Kitchen Light", "expected": "on"}
    )
    assert "object" in result


def test_provider_rejects_an_unknown_tool():
    assert "Unknown tool" in DiagnosticsProvider(_service()).execute("nope", {})
