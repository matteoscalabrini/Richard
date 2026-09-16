# Tools: clock, forecast, web search Implementation Plan (round 2, plan B of 3)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Give Richard three tools he lacked in the 2026-09-16 session: the time, the weather forecast, and web search.

**Architecture:** `clock` is an always-on provider next to memory. `forecast` extends the Home Assistant plugin using `weather.get_forecasts` with `return_response`. `web_search` is a new plugin backed by Tavily (Matteo's choice, 2026-09-16), key read from a file path in its config table, never from the TOML. Branch `reachy-presence`, worktree `/Users/matteo/Documents/GitHub/Richard/.worktrees/reachy-presence`. Runs after Plan A (`2026-09-16-conversation-hygiene.md`), which provides `richard.clock`.

**Tech Stack:** Python 3.12, httpx (already a dependency; tests use `httpx.MockTransport`), pytest.

## Global Constraints

- Tool results are short English strings; the model answers in the user's language. No global truncation exists, so each tool caps its own output.
- Secrets: the Tavily key lives in a file (`api_key_file`, default `~/.richard/tavily.key`), read at build time, never logged, never written to config.
- Plugins register in `pyproject.toml` `[project.entry-points."richard.plugins"]` and need a package reinstall on the CT (`uv pip install -e .`).
- Commit after each task with the message given. No pushes.

---

### Task 1: `clock` tool

**Files:**
- Create: `src/richard/providers/clock.py`
- Modify: `src/richard/cli.py` (`_engine_providers`, add `ClockProvider(tz_name=config.timezone or None)` after the memory provider)
- Test: `tests/test_providers_clock.py`

**Interfaces:** `ClockProvider(tz_name=None, now=None)`; schema `clock` with no parameters, description "The current date and time, with weekday and timezone. Use it whenever the user asks about the time, the date, the day of the week, or how long until or since something."; `execute("clock", {})` → `"Wednesday 2026-09-16 19:31 CEST (Europe/Rome)"` (zone name omitted when unset); `context()` → `None`.

- [ ] **Step 1: Failing test**
```python
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
```
- [ ] **Step 2: Run** → `ModuleNotFoundError`.
- [ ] **Step 3: Implement**
```python
"""The time, as a tool: the head is pinned for the cache, so the clock cannot live there."""
from __future__ import annotations

from collections.abc import Callable
from datetime import datetime, tzinfo

from richard.clock import _zone, local_now

CLOCK_SCHEMA = {"type": "function", "function": {
    "name": "clock",
    "description": ("The current date and time, with weekday and timezone. Use it whenever the user "
                    "asks about the time, the date, the day of the week, or how long until or since something."),
    "parameters": {"type": "object", "properties": {}}}}


class ClockProvider:
    def __init__(self, tz_name: str | None = None, now: Callable[[tzinfo | None], datetime] | None = None) -> None:
        self._tz_name = tz_name
        self._now = now

    def schemas(self) -> list[dict]:
        return [CLOCK_SCHEMA]

    def execute(self, name: str, arguments: dict) -> str:
        if name != "clock":
            return f"Unknown tool: {name}."
        zone = _zone(self._tz_name)
        moment = self._now(zone) if self._now is not None else local_now(self._tz_name)
        text = moment.strftime("%A %Y-%m-%d %H:%M %Z")
        return f"{text} ({self._tz_name})" if self._tz_name and zone is not None else text

    def context(self) -> str | None:
        return None
```
(`_zone` is private in `clock.py`; rename it to `zone_for` there and update the A4 usages, or import it as is and note it. Prefer the rename.)
- [ ] **Step 4: Run** the test, wire `cli.py`, run the suite.
- [ ] **Step 5: Commit** `providers: clock tool`

---

### Task 2: `forecast` tool on Home Assistant

**Files:**
- Modify: `src/richard/plugins/home_assistant/client.py` (add `call_service_with_response(domain, service, data) -> dict`), `src/richard/plugins/home_assistant/provider.py` (schema + execute + a `weather_entity` setting), `src/richard/config.py` (`HomeAssistant.weather_entity: str = ""` in `from_table`/`to_table`)
- Test: `tests/test_home_assistant_client.py`, `tests/test_provider_home_assistant.py`

**Interfaces:**
- `HomeAssistantClient.call_service_with_response(domain, service, data)` POSTs `/api/services/{domain}/{service}?return_response` and returns the parsed JSON dict (HA returns `{"changed_states": [...], "service_response": {...}}`); raises `HomeAssistantError` on non-dict payloads.
- `forecast` schema: params `days` (integer 1-5, default 2), `hourly` (boolean, default false: hourly for the next 12 hours instead of daily). Execute: entity = `weather_entity` if set, else the first `weather.*` entity from `list_entities()`; call `weather.get_forecasts` with `{"entity_id": id, "type": "hourly" if hourly else "daily"}`; format the entity's current state first, then one line per period, capped at `days` (daily) or 12 (hourly):
  `now: partlycloudy, 21.4°C, humidity 60%, wind 12 km/h` then
  `Wed 2026-09-17: partlycloudy 26°/15°, rain 20%` or `19:00: rainy 18°, rain 80%`.
  Missing keys are skipped, not errors. No weather entity → "No weather entity is available in Home Assistant."

- [ ] **Step 1: Failing tests**
Client: a `MockTransport` handler asserting the URL ends with `/api/services/weather/get_forecasts?return_response` and the JSON body, returning `{"changed_states": [], "service_response": {"weather.home": {"forecast": [{"datetime": "2026-09-17T00:00:00+00:00", "condition": "partlycloudy", "temperature": 26, "templow": 15, "precipitation_probability": 20}]}}}`; assert the method returns the dict.
Provider: with the file's `FakeClient` stub extended with `call_service_with_response`, a `weather.home` entity (state `partlycloudy`, attributes `temperature 21.4, humidity 60, wind_speed 12`), `execute("forecast", {"days": 1})` returns exactly
`"weather.home now: partlycloudy, 21.4°C, humidity 60%, wind 12 km/h; Thu 2026-09-17: partlycloudy 26°/15°, rain 20%"` (compute the weekday from the datetime; 2026-09-17 is a Thursday). Also: `hourly: true` formats `HH:MM: condition temp°, rain N%`; no weather entity → the sentence above; `days` clamped to 1..5.
- [ ] **Step 2: Run** → failures.
- [ ] **Step 3: Implement**: client method reusing `_request` with `params={"return_response": ""}`; provider `FORECAST_SCHEMA` with description "Weather forecast from Home Assistant for the coming days (or the next hours with hourly=true). Use it for any question about upcoming weather, rain, temperature or what to wear."; formatting helpers `_daily_line(period)` / `_hourly_line(period)` with `°` and the weekday abbreviation from `datetime.fromisoformat(period["datetime"])`; register the schema in `schemas()` and the branch in `execute`; `weather_entity` config plumbed through `HomeAssistant.from_table/to_table` and into the provider constructor in `plugins/home_assistant/__init__.py`.
- [ ] **Step 4: Run** both test files, `tests/test_config.py`, the suite.
- [ ] **Step 5: Commit** `home_assistant: forecast tool via weather.get_forecasts`

---

### Task 3: `web_search` plugin on Tavily

**Files:**
- Create: `src/richard/plugins/web_search/__init__.py`, `src/richard/plugins/web_search/tavily.py`, `src/richard/plugins/web_search/provider.py`
- Modify: `pyproject.toml` (entry point `web_search = "richard.plugins.web_search:WebSearchPlugin"`)
- Test: `tests/test_web_search_tavily.py`, `tests/test_plugin_web_search.py`

**Interfaces:**
- `TavilyClient(api_key, *, timeout=8.0, client: httpx.Client | None = None)`; `search(query, *, recent=False, max_results=3) -> SearchResult(answer: str, results: list[Hit(title, url, content)])`. POST `https://api.tavily.com/search`, header `Authorization: Bearer <key>`, JSON `{"query", "search_depth": "basic", "include_answer": "basic", "max_results", "topic": "news" if recent else "general", "time_range": "week" if recent else None}` (omit None). Errors: 401 → `WebSearchError("the web search key was rejected")`, 429/432/433 → `WebSearchError("web search quota exhausted")`, other → `WebSearchError(f"web search failed: HTTP {code}")`, transport → `WebSearchError("web search unreachable")`.
- `WebSearchProvider(client)`: schema `web_search` params `query` (string, required), `recent` (boolean: "true for news or anything that changed in the last days"). Execute → `"{answer}\nSources: 1. {title} — {url}; 2. …"` with `content` snippets omitted when an answer exists, else the top-3 snippets truncated to 200 chars each. `context()` → "Web search is available with the web_search tool for facts you do not know or that may have changed; report what the tool returned and name the source, never assert beyond it."
- `WebSearchPlugin`: `name = "web_search"`, `config_defaults() -> {"api_key_file": "~/.richard/tavily.key", "max_results": 3}`; `build` reads the file (expanduser, strip), raises `ValueError("web_search: api key file <path> is missing or empty")` when absent (the registry then disables the plugin with that message).

- [ ] **Step 1: Failing tests**
`tests/test_web_search_tavily.py` (MockTransport): request URL/headers/body as above for `recent=True`; response `{"answer": "It opened in 1981.", "results": [{"title": "Merrily", "url": "https://x/y", "content": "…"}]}` parsed into `SearchResult`; 401 and 429 map to the two messages; a `httpx.ConnectError` maps to "unreachable".
`tests/test_plugin_web_search.py`: provider formatting with a stub client (answer present → answer + sources line; no answer → snippets); `WebSearchPlugin().build(ctx)` with a tmp key file returns one provider; missing file raises `ValueError` with the path in the message; `config_defaults()` exact dict.
- [ ] **Step 2: Run** → failures.
- [ ] **Step 3: Implement** the three modules following `plugins/home_assistant/client.py` for the httpx pattern (`client=` seam, `_headers`, `raise_for_status` mapping) and `plugins/perception/__init__.py` for the plugin shape. Add the entry point.
- [ ] **Step 4: Run** the two test files and the suite. Then `uv pip install -e .` locally is not needed for tests (entry points are only read by `discover()` at serve time; add a registry test only if `tests/test_plugins_registry.py` already covers entry-point discovery with a fake).
- [ ] **Step 5: Commit** `plugins: web_search on Tavily`

---

## Self-review
- Items covered: 9 (tool side) → B1, 10 → B2, 11 → B3.
- B1 depends on Plan A's `richard.clock` (`local_now`, `_zone` renamed `zone_for`).
- Enabling on the CT (Plan C's deploy task): `richard plugins enable web_search`, key file present, `weather_entity` optional.
