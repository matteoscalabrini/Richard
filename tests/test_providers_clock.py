from datetime import datetime, timezone

from richard.providers.clock import ClockProvider


def test_clock_schema_and_answer():
    fixed = datetime(2026, 9, 16, 17, 31, tzinfo=timezone.utc)
    provider = ClockProvider(tz_name="Europe/Rome", now=lambda tz: fixed.astimezone(tz))
    (schema,) = provider.schemas()
    assert schema["function"]["name"] == "clock"
    assert schema["function"]["parameters"] == {"type": "object", "properties": {}}
    assert provider.execute("clock", {}) == "Wednesday 2026-09-16 19:31 CEST (Europe/Rome)"
    assert provider.execute("other", {}) == "Unknown tool: other."
    assert provider.context() is None
