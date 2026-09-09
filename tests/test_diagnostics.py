from richard.diagnostics import DiagnosticsProvider, DiagnosticsService
from richard.errors import HomeAssistantError
from richard.plugins.home_assistant.client import HomeAssistantEntity


class FakeHomeAssistant:
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
        raise HomeAssistantError("Home Assistant entity or service was not found")


def _service(home_assistant=None):
    return DiagnosticsService(home_assistant=home_assistant or FakeHomeAssistant())


# --- refresh ---


def test_refresh_without_home_assistant_reports_it_as_unconfigured():
    report = DiagnosticsService().refresh()
    assert report.ha_configured is False
    assert report.ha_entity_count == 0
    assert report.ha_error is None
    assert "not configured" in report.summary()


def test_refresh_fetches_the_live_home_assistant_inventory():
    report = _service().refresh()
    assert report.status == "ok"
    assert report.ha_configured is True and report.ha_reachable is True
    assert report.ha_entity_count == 2
    assert "2 entities" in report.summary()


def test_refresh_reports_an_unreachable_home_assistant_as_partial():
    report = _service(FakeHomeAssistant(error="Home Assistant is unreachable")).refresh()
    assert report.status == "partial"
    assert report.ha_reachable is False
    assert "unreachable" in report.ha_error
    assert "unreachable" in report.summary()


# --- diagnose ---


def test_diagnose_resolves_a_home_assistant_entity():
    diagnosis = _service().diagnose("Kitchen Light")
    assert diagnosis.target == "light.kitchen"
    assert diagnosis.reachable is True
    assert diagnosis.state["state"] == "on"
    message = diagnosis.message()
    assert "Kitchen Light" in message and "light.kitchen" in message


def test_diagnose_reports_ambiguity_without_acting():
    home_assistant = FakeHomeAssistant(
        entities=[
            HomeAssistantEntity("light.kitchen", "on", {"friendly_name": "Kitchen Light"}),
            HomeAssistantEntity("switch.kitchen", "off", {"friendly_name": "Kitchen Light"}),
        ]
    )
    diagnosis = _service(home_assistant).diagnose("kitchen light")
    assert diagnosis.ambiguous
    message = diagnosis.message()
    assert "more than one" in message
    assert "ha:light.kitchen" in message and "ha:switch.kitchen" in message


def test_diagnose_reports_an_unreachable_home_assistant():
    matches = FakeHomeAssistant()

    class FlakyHomeAssistant(FakeHomeAssistant):
        def get_entity(self, entity_id):
            raise HomeAssistantError("timed out")

    diagnosis = _service(FlakyHomeAssistant(entities=matches.entities)).diagnose(
        "Kitchen Light"
    )
    assert diagnosis.reachable is False
    assert "timed out" in diagnosis.error
    assert "timed out" in diagnosis.message()


def test_diagnose_unknown_target_is_reported():
    diagnosis = _service().diagnose("Toaster")
    assert diagnosis.reachable is False
    assert "Toaster" in diagnosis.message()


# --- verify ---


def test_verify_checks_home_assistant_state_and_attributes():
    home_assistant = FakeHomeAssistant(
        entities=[
            HomeAssistantEntity(
                "climate.hall", "heat", {"friendly_name": "Hall", "temperature": 21}
            )
        ]
    )
    service = _service(home_assistant)
    confirmed = service.verify("Hall", {"state": "heat", "temperature": 21})
    assert confirmed.confirmed is True
    assert confirmed.message().startswith("CONFIRMED: ")
    assert service.verify("Hall", {"temperature": 18}).message().startswith("MISMATCH: ")


def test_verify_reports_unconfirmed_when_the_target_cannot_be_read():
    class FlakyHomeAssistant(FakeHomeAssistant):
        def get_entity(self, entity_id):
            raise HomeAssistantError("timed out")

    result = _service(FlakyHomeAssistant()).verify("Kitchen Light", {"state": "on"})
    assert result.message().startswith("UNCONFIRMED: ")


def test_verify_reports_unconfirmed_for_a_missing_field():
    result = _service().verify("Kitchen Light", {"brightness": 80})
    assert result.message().startswith("UNCONFIRMED: ")
    assert "does not report" in result.message()


def test_verify_reports_failed_for_an_unknown_target():
    result = _service().verify("Toaster", {"state": "on"})
    assert result.message().startswith("FAILED: ")


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


def test_provider_refresh_returns_a_summary():
    provider = DiagnosticsProvider(_service())
    assert "2 entities" in provider.execute("refresh_devices", {})


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
