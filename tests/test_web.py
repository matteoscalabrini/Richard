import asyncio
import json
import threading

import pytest

from richard.config import Config, HomeAssistant, load_config, save_config
from richard.control_loops import ControlLoopStore, ControlTargetReader
from richard.errors import HomeAssistantError
from richard.plugins.home_assistant.client import HomeAssistantEntity
from richard.plugins.home_assistant.reader import HomeAssistantTargetReader
from richard.memory import MemoryStore
from richard.satellite.relays import RelayRegistry
from richard.web import WebApp
from richard.web.app import _format_response


class FakeTargetHomeAssistant:
    """Backs the control-loop target reader — no sockets."""

    def __init__(self, entities=()):
        self.entities = {entity.entity_id: entity for entity in entities}

    def list_entities(self):
        return list(self.entities.values())

    def get_entity(self, entity_id):
        return self.entities[entity_id]


def _app(
    tmp_path,
    relays=None,
    memory=None,
    control_loops=None,
    home_assistant_client_factory=None,
    entities=(),
    voice_library_factory=None,
    plugin_records=None,
):
    config_path = tmp_path / "config.toml"
    save_config(Config(), config_path)
    control_loops = control_loops or ControlLoopStore(":memory:")
    kwargs = {}
    if home_assistant_client_factory is not None:
        kwargs["home_assistant_client_factory"] = home_assistant_client_factory
    if voice_library_factory is not None:
        kwargs["voice_library_factory"] = voice_library_factory
    if plugin_records is not None:
        kwargs["plugin_records"] = plugin_records
    return WebApp(
        config_path=config_path,
        memory_store=memory or MemoryStore(":memory:"),
        control_loop_store=control_loops,
        control_target_reader=ControlTargetReader(
            readers={"ha": HomeAssistantTargetReader(FakeTargetHomeAssistant(entities))}
        ),
        relays=relays or RelayRegistry(),
        **kwargs,
    )


def _body(resp):
    return json.loads(resp.body)


# --- routing ---


def test_index_serves_html(tmp_path):
    app = _app(tmp_path)
    resp = app.handle("GET", "/")
    assert resp.status == 200
    assert resp.content_type == "text/html; charset=utf-8"
    assert resp.headers == {"Cache-Control": "no-store"}
    assert b"<title>Richard" in resp.body


def test_index_html_alias(tmp_path):
    app = _app(tmp_path)
    resp = app.handle("GET", "/index.html")
    assert resp.status == 200


def test_unknown_route_is_404(tmp_path):
    app = _app(tmp_path)
    resp = app.handle("GET", "/nope")
    assert resp.status == 404


def test_method_not_allowed_on_config(tmp_path):
    app = _app(tmp_path)
    resp = app.handle("DELETE", "/api/config")
    assert resp.status == 404  # only GET/PUT are routed for /api/config


# --- /api/status ---


def test_status_reports_version_and_satellites(tmp_path):
    relays = RelayRegistry()
    relays.register("HUB1", object())
    relays.register("HUB2", object())
    app = _app(tmp_path, relays=relays)
    resp = app.handle("GET", "/api/status")
    assert resp.status == 200
    data = _body(resp)
    assert data["ok"] is True
    assert data["version"]
    assert sorted(data["satellites"]) == ["HUB1", "HUB2"]
    assert data["control_loops"] == 0
    assert data["unread_notifications"] == 0


def test_status_with_no_relays(tmp_path):
    app = _app(tmp_path)
    data = _body(app.handle("GET", "/api/status"))
    assert data["satellites"] == []


# --- /api/config GET ---


def test_get_config_returns_full_shape(tmp_path):
    app = _app(tmp_path)
    data = _body(app.handle("GET", "/api/config"))
    assert data["llm_endpoint"] == "http://localhost:8080"
    assert data["personality"]["name"] == "Richard"
    assert data["voice"]["stt_engine"] == "local"
    assert data["home_assistant"]["enabled"] is False
    assert data["home_assistant"]["host"] == "homeassistant.local"
    assert data["home_assistant"]["port"] == 8123
    assert data["home_assistant"]["url"] == "http://homeassistant.local:8123"
    assert data["home_assistant"]["token"] == ""
    assert data["home_assistant"]["token_configured"] is False
    assert data["satellite"]["port"] == 8770
    assert data["web"]["port"] == 8771
    assert data["web"]["enabled"] is True


def test_config_payload_includes_realtime_section(tmp_path):
    app = _app(tmp_path)
    resp = app.handle("GET", "/api/config", b"")
    data = json.loads(resp.body)
    assert data["realtime"] == {"enabled": True, "host": "0.0.0.0", "port": 8766, "token": ""}


# --- /api/config PUT ---


def test_put_config_updates_and_persists(tmp_path):
    app = _app(tmp_path)
    patch = {
        "llm_endpoint": "http://brain:8080",
        "llm_model": "Gemma4",
        "personality": {"name": "Tars", "humour": 100, "honesty": 85, "directness": 40},
        "voice": {"tts_engine": "piper", "tts_voice": "TARS", "vad_aggressiveness": 3},
        "home_assistant": {
            "enabled": True,
            "host": "ha.local",
            "port": 8443,
            "use_https": True,
            "token": "secret",
            "timeout": 15,
            "verify_ssl": False,
        },
        "web": {"port": 9000, "enabled": False},
    }
    resp = app.handle("PUT", "/api/config", json.dumps(patch).encode())
    assert resp.status == 200
    data = _body(resp)
    assert "llm_endpoint" in data["changed"]
    assert "personality.name" in data["changed"]
    assert "voice.tts_engine" in data["changed"]
    assert "home_assistant.enabled" in data["changed"]
    assert "web.port" in data["changed"]
    assert data["config"]["llm_endpoint"] == "http://brain:8080"
    assert data["config"]["personality"]["name"] == "Tars"
    assert data["config"]["personality"]["humour"] == 100
    assert data["config"]["voice"]["tts_engine"] == "piper"
    assert data["config"]["home_assistant"]["host"] == "ha.local"
    assert data["config"]["home_assistant"]["port"] == 8443
    assert data["config"]["home_assistant"]["url"] == "https://ha.local:8443"
    assert data["config"]["home_assistant"]["token"] == ""
    assert data["config"]["home_assistant"]["token_configured"] is True
    assert data["config"]["web"]["port"] == 9000
    assert data["config"]["web"]["enabled"] is False
    # persisted to disk
    reloaded = load_config(app._config_path)
    assert reloaded.llm_endpoint == "http://brain:8080"
    assert reloaded.personality.name == "Tars"
    assert reloaded.voice.tts_engine == "piper"
    assert reloaded.home_assistant.enabled is True
    assert reloaded.home_assistant.host == "ha.local"
    assert reloaded.home_assistant.port == 8443
    assert reloaded.home_assistant.use_https is True
    assert reloaded.home_assistant.token == "secret"
    assert reloaded.home_assistant.verify_ssl is False
    assert reloaded.plugins.tables["home_assistant"]["host"] == "ha.local"
    assert reloaded.web.port == 9000
    assert reloaded.web.enabled is False


def test_put_config_clamps_dials(tmp_path):
    app = _app(tmp_path)
    resp = app.handle("PUT", "/api/config", json.dumps({"personality": {"humour": 999}}).encode())
    assert _body(resp)["config"]["personality"]["humour"] == 100


def test_put_config_empty_patch_is_noop(tmp_path):
    app = _app(tmp_path)
    resp = app.handle("PUT", "/api/config", b"{}")
    assert resp.status == 200
    assert _body(resp)["changed"] == []


def test_put_config_invalid_json(tmp_path):
    app = _app(tmp_path)
    resp = app.handle("PUT", "/api/config", b"not json")
    assert resp.status == 400
    assert "invalid JSON" in _body(resp)["error"]


def test_put_config_non_object(tmp_path):
    app = _app(tmp_path)
    resp = app.handle("PUT", "/api/config", b"[1,2,3]")
    assert resp.status == 400


def test_put_config_rejects_malformed_scalar(tmp_path):
    # A buggy client sending a nested object where a scalar is expected must get a
    # clean 400 (not a 500), and nothing may be persisted. Regression for the web
    # UI bug where top-level LLM fields were wrapped as {"llm_timeout": {...}}.
    app = _app(tmp_path)
    before = load_config(app._config_path).llm_timeout
    resp = app.handle("PUT", "/api/config", json.dumps({"llm_timeout": {"x": 1}}).encode())
    assert resp.status == 400
    assert "invalid config value" in _body(resp)["error"]
    assert load_config(app._config_path).llm_timeout == before


def test_put_config_clears_api_key_with_empty_string(tmp_path):
    app = _app(tmp_path)
    # set a key first
    app.handle("PUT", "/api/config", json.dumps({"llm_api_key": "secret"}).encode())
    assert load_config(app._config_path).llm_api_key == "secret"
    # now clear it
    app.handle("PUT", "/api/config", json.dumps({"llm_api_key": ""}).encode())
    assert load_config(app._config_path).llm_api_key is None


def test_put_config_clears_home_assistant_token_and_clamps_timeout(tmp_path):
    app = _app(tmp_path)
    app.handle(
        "PUT",
        "/api/config",
        json.dumps({"home_assistant": {"token": "secret", "timeout": 999}}).encode(),
    )
    ha = load_config(app._config_path).home_assistant
    assert ha.token == "secret"
    assert ha.timeout == 300.0
    app.handle(
        "PUT", "/api/config", json.dumps({"home_assistant": {"token": ""}}).encode()
    )
    assert load_config(app._config_path).home_assistant.token is None


def test_web_ui_has_conversation_first_home_shell(tmp_path):
    html = _app(tmp_path).handle("GET", "/").body.decode()

    assert '<main id="richard-home"' in html
    assert 'id="richard-entity"' in html
    assert '<img src="/icon"' in html
    assert 'id="entity-state"' in html
    assert 'id="latest-reply"' in html
    assert "Ready when you are." in html
    assert 'id="chat-input"' in html
    assert 'id="chat-send"' in html
    assert 'id="chat-mic"' in html
    assert 'id="menu-toggle"' in html
    assert 'aria-expanded="false"' in html
    assert 'id="drawer-backdrop"' in html


def test_web_ui_has_two_stage_drawer_and_all_page_destinations(tmp_path):
    html = _app(tmp_path).handle("GET", "/").body.decode()

    assert 'id="richard-drawer"' in html
    assert 'data-drawer-page="menu"' in html
    assert 'data-drawer-page="configuration"' in html
    for page in (
        "conversation",
        "automations",
        "memory",
        "system",
        "brain",
        "personality",
        "voice",
        "home-assistant",
        "satellite",
        "web-access",
    ):
        assert f'data-drawer-page="{page}"' in html
    assert 'data-drawer-open="configuration"' in html
    assert 'data-drawer-back="menu"' in html
    assert 'data-drawer-back="configuration"' in html
    assert "function showDrawerPage(name)" in html
    assert "function openDrawer()" in html
    assert "function closeDrawer()" in html


def test_web_ui_drives_latest_reply_history_and_entity_states(tmp_path):
    html = _app(tmp_path).handle("GET", "/").body.decode()

    assert "function setEntityState(state)" in html
    assert "function setLatestReply(text, kind)" in html
    assert "function renderChatHistory()" in html
    assert "setEntityState('listening')" in html
    assert "setEntityState('thinking')" in html
    assert "setEntityState('speaking')" in html
    assert "setEntityState('ready')" in html
    assert "setEntityState('offline')" in html
    assert "setLatestReply(reply" in html


def test_web_ui_drawer_is_accessible_responsive_and_reduced_motion_safe(tmp_path):
    html = _app(tmp_path).handle("GET", "/").body.decode()

    assert 'aria-controls="richard-drawer"' in html
    assert 'aria-label="Richard menu"' in html
    assert "e.key === 'Escape' &&" in html
    assert "prefers-reduced-motion: reduce" in html
    assert "env(safe-area-inset-bottom)" in html
    assert "@media (max-width: 700px)" in html
    assert ".richard-drawer { width: 100%" in html
    assert "function trapDrawerFocus(e)" in html
    assert "const focusable = active.querySelectorAll" in html


def test_web_ui_has_home_assistant_host_port_token_fields_and_collapsible_memory(tmp_path):
    html = _app(tmp_path).handle("GET", "/").body.decode()
    assert 'id="home_assistant.host"' in html
    assert 'id="home_assistant.port"' in html
    assert 'id="home_assistant.token"' in html
    assert 'id="home-assistant-test"' in html
    assert 'id="home-assistant-restart"' in html
    assert 'id="home-assistant-entity-filter"' in html
    assert 'id="home-assistant-entity-list"' in html
    assert "/api/home-assistant" in html
    assert "const MEMORY_COLLAPSED_LIMIT = 12" in html
    assert "memories.slice(-MEMORY_COLLAPSED_LIMIT)" in html
    assert 'id="memory-toggle"' in html
    assert 'id="control-loop-targets"' in html
    assert 'id="control-loop-trigger"' in html
    assert "/api/control-loop-notifications" in html


# --- /api/home-assistant ---


def test_home_assistant_status_reports_disabled_without_connecting(tmp_path):
    app = _app(
        tmp_path,
        home_assistant_client_factory=lambda *args, **kwargs: (_ for _ in ()).throw(
            AssertionError("disabled integration must not connect")
        ),
    )

    response = app.handle("GET", "/api/home-assistant")

    assert response.status == 200
    data = _body(response)
    assert data["enabled"] is False
    assert data["connected"] is False
    assert "disabled" in data["error"].lower()
    assert data["entities"] == []


def test_home_assistant_status_tests_connection_and_lists_safe_entity_summary(tmp_path):
    calls = []

    class Client:
        def __init__(self, url, token, **kwargs):
            calls.append((url, token, kwargs))

        def list_entities(self):
            return [
                HomeAssistantEntity(
                    "light.kitchen", "on", {"friendly_name": "Kitchen Light"}
                ),
                HomeAssistantEntity(
                    "sensor.kitchen_temperature",
                    "21.5",
                    {"friendly_name": "Kitchen Temperature"},
                ),
            ]

    app = _app(tmp_path, home_assistant_client_factory=Client)
    config = load_config(app._config_path)
    config.set_home_assistant(HomeAssistant(enabled=True, host="ha.local", token="super-secret"))
    save_config(config, app._config_path)

    response = app.handle("GET", "/api/home-assistant")

    assert response.status == 200
    data = _body(response)
    assert data["connected"] is True
    assert data["entity_count"] == 2
    assert data["domains"] == {"light": 1, "sensor": 1}
    assert data["entities"][0] == {
        "entity_id": "light.kitchen",
        "name": "Kitchen Light",
        "domain": "light",
        "state": "on",
    }
    assert b"super-secret" not in response.body
    assert calls == [("http://ha.local:8123", "super-secret", {"timeout": 10.0, "verify_ssl": True})]


def test_home_assistant_status_returns_connection_error_as_feedback(tmp_path):
    class Client:
        def __init__(self, *args, **kwargs):
            pass

        def list_entities(self):
            raise HomeAssistantError("Home Assistant rejected the access token")

    app = _app(tmp_path, home_assistant_client_factory=Client)
    config = load_config(app._config_path)
    config.set_home_assistant(HomeAssistant(enabled=True, token="bad-token"))
    save_config(config, app._config_path)

    response = app.handle("GET", "/api/home-assistant")

    assert response.status == 200
    data = _body(response)
    assert data["connected"] is False
    assert data["entity_count"] == 0
    assert data["error"] == "Home Assistant rejected the access token"


# --- /api/control-loops + notifications ---


def test_control_loop_api_create_list_pause_and_delete_target(tmp_path):
    app = _app(
        tmp_path,
        entities=[HomeAssistantEntity("light.desk", "off", {"friendly_name": "Desk Lamp"})],
    )
    payload = {
        "name": "Late lamp",
        "targets": ["Desk Lamp"],
        "trigger_description": "If it turns on after midnight, turn it off.",
        "interval_seconds": 10,
    }
    created = app.handle("POST", "/api/control-loops", json.dumps(payload).encode())
    assert created.status == 201
    loop = _body(created)
    assert loop["targets"] == ["ha:light.desk"]
    assert loop["has_baseline"] is False

    listed = _body(app.handle("GET", "/api/control-loops"))["control_loops"]
    assert [item["name"] for item in listed] == ["Late lamp"]
    paused = app.handle(
        "PUT", f"/api/control-loops/{loop['id']}", json.dumps({"enabled": False}).encode()
    )
    assert _body(paused)["enabled"] is False
    deleted = app.handle("DELETE", f"/api/control-loops/{loop['id']}")
    assert _body(deleted)["deleted"] == loop["id"]


def test_control_loop_api_rejects_unknown_target_and_short_interval(tmp_path):
    app = _app(
        tmp_path,
        entities=[HomeAssistantEntity("light.lamp", "off", {"friendly_name": "Lamp"})],
    )
    base = {
        "name": "Bad loop",
        "targets": ["Unknown Lamp"],
        "trigger_description": "Any change",
        "interval_seconds": 10,
    }
    assert app.handle("POST", "/api/control-loops", json.dumps(base).encode()).status == 400
    base["targets"] = ["Lamp"]
    base["interval_seconds"] = 1
    assert app.handle("POST", "/api/control-loops", json.dumps(base).encode()).status == 400


def test_control_loop_api_roundtrips_schedule(tmp_path):
    app = _app(tmp_path)
    payload = {
        "name": "Morning",
        "targets": [],
        "trigger_description": "Check the house.",
        "schedule": {"at": "08:00"},
    }
    created = _body(app.handle("POST", "/api/control-loops", json.dumps(payload).encode()))
    assert created["kind"] == "scheduled"
    assert created["schedule"] == {"at": "08:00"}
    assert created["next_run_at"]

    listed = _body(app.handle("GET", "/api/control-loops"))["control_loops"]
    assert listed[0]["kind"] == "scheduled"

    updated = app.handle(
        "PUT",
        f"/api/control-loops/{created['id']}",
        json.dumps({"schedule": {"every_seconds": 600}}).encode(),
    )
    assert _body(updated)["schedule"] == {"every_seconds": 600.0}


def test_control_loop_api_rejects_invalid_schedule(tmp_path):
    app = _app(tmp_path)
    payload = {"name": "x", "targets": [], "trigger_description": "t", "schedule": {"at": "8am"}}
    resp = app.handle("POST", "/api/control-loops", json.dumps(payload).encode())
    assert resp.status == 400


def test_control_loop_api_rejects_malformed_targets_on_scheduled_loop(tmp_path):
    app = _app(tmp_path)
    # Create a scheduled control loop with empty targets
    payload = {
        "name": "Morning",
        "targets": [],
        "trigger_description": "Check the house.",
        "schedule": {"at": "08:00"},
    }
    created = _body(app.handle("POST", "/api/control-loops", json.dumps(payload).encode()))
    loop_id = created["id"]

    # Try to update with null targets (malformed, should be rejected)
    resp = app.handle(
        "PUT",
        f"/api/control-loops/{loop_id}",
        json.dumps({"targets": None}).encode(),
    )
    assert resp.status == 400


def test_control_loop_notification_api_lists_and_dismisses(tmp_path):
    store = ControlLoopStore(":memory:")
    notification = store.add_notification(
        loop_id=1,
        loop_name="Safety",
        summary="Lamp changed",
        response="The lamp turned on.",
    )
    app = _app(tmp_path, control_loops=store)
    listed = _body(app.handle("GET", "/api/control-loop-notifications"))["notifications"]
    assert listed[0]["response"] == "The lamp turned on."
    assert app.handle(
        "DELETE", f"/api/control-loop-notifications/{notification.id}"
    ).status == 200
    assert _body(app.handle("GET", "/api/control-loop-notifications"))["notifications"] == []


def test_home_assistant_inventory_runs_off_the_event_loop(tmp_path):
    from richard.web.app import _serve_request

    release = threading.Event()

    class BlockingClient:
        def __init__(self, *a, **k):
            pass

        def list_entities(self):
            # Only a second request — served by the same loop — releases this. If the
            # fetch ran on the loop, that request could never be answered and the
            # test would deadlock rather than pass.
            if not release.wait(timeout=5):
                raise AssertionError("event loop was blocked by the inventory fetch")
            return []

    config_path = tmp_path / "config.toml"
    config = Config()
    config.set_home_assistant(HomeAssistant(enabled=True, host="ha.local", token="token"))
    save_config(config, config_path)
    app = WebApp(
        config_path=config_path,
        memory_store=MemoryStore(":memory:"),
        control_loop_store=ControlLoopStore(":memory:"),
        relays=RelayRegistry(),
        home_assistant_client_factory=BlockingClient,
    )

    async def _handle(reader, writer):
        try:
            await _serve_request(app, reader, writer)
        except Exception:
            pass
        finally:
            writer.close()

    async def run():
        server = await asyncio.start_server(_handle, "127.0.0.1", 0)
        port = server.sockets[0].getsockname()[1]
        try:
            slow_r, slow_w = await asyncio.open_connection("127.0.0.1", port)
            slow_w.write(
                b"GET /api/home-assistant HTTP/1.1\r\nHost: localhost\r\n"
                b"Connection: close\r\n\r\n"
            )
            await slow_w.drain()
            fast_r, fast_w = await asyncio.open_connection("127.0.0.1", port)
            fast_w.write(
                b"GET /api/status HTTP/1.1\r\nHost: localhost\r\nConnection: close\r\n\r\n"
            )
            await fast_w.drain()
            fast = await fast_r.read()  # answered while the fetch is still blocked
            release.set()
            slow = await slow_r.read()
            for writer in (slow_w, fast_w):
                writer.close()
            return fast, slow
        finally:
            server.close()
            await server.wait_closed()

    fast, slow = asyncio.run(asyncio.wait_for(run(), timeout=10))
    assert '"ok": true' in fast.decode()
    assert "HTTP/1.1 200 OK" in slow.decode()


# --- /api/memories ---


def test_list_memories_empty(tmp_path):
    app = _app(tmp_path)
    data = _body(app.handle("GET", "/api/memories"))
    assert data["memories"] == []


def test_add_memory_then_list_then_delete(tmp_path):
    app = _app(tmp_path)
    # add
    resp = app.handle("POST", "/api/memories", json.dumps({"text": "likes coffee"}).encode())
    assert resp.status == 201
    mem = _body(resp)
    assert mem["text"] == "likes coffee"
    assert mem["person"] == "you"
    mem_id = mem["id"]
    # list
    listed = _body(app.handle("GET", "/api/memories"))["memories"]
    assert len(listed) == 1
    assert listed[0]["id"] == mem_id
    # delete
    resp = app.handle("DELETE", f"/api/memories/{mem_id}")
    assert resp.status == 200
    assert _body(resp)["forgotten"] == mem_id
    # list again
    assert _body(app.handle("GET", "/api/memories"))["memories"] == []


def test_add_memory_requires_text(tmp_path):
    app = _app(tmp_path)
    resp = app.handle("POST", "/api/memories", b"{}")
    assert resp.status == 400
    assert "text" in _body(resp)["error"]


def test_add_memory_with_person(tmp_path):
    app = _app(tmp_path)
    resp = app.handle("POST", "/api/memories", json.dumps({"text": "hi", "person": "alice"}).encode())
    assert _body(resp)["person"] == "alice"


def test_delete_memory_not_found(tmp_path):
    app = _app(tmp_path)
    resp = app.handle("DELETE", "/api/memories/999")
    assert resp.status == 404


def test_delete_memory_bad_id(tmp_path):
    app = _app(tmp_path)
    resp = app.handle("DELETE", "/api/memories/abc")
    assert resp.status == 400


# --- handler robustness ---


def test_handler_swallows_exceptions(tmp_path):
    app = _app(tmp_path)

    def boom(*a, **k):
        raise RuntimeError("boom")

    app._load = boom
    resp = app.handle("GET", "/api/config")
    assert resp.status == 500
    assert "boom" in _body(resp)["error"]


# --- HTTP response formatting ---


def test_format_response_headers():
    resp = WebApp.__new__(WebApp)  # bypass __init__ for a pure formatting test
    from richard.web.app import Response

    raw = _format_response(Response.json({"ok": True}), keep_alive=True)
    head = raw.split(b"\r\n\r\n", 1)[0].decode()
    assert "HTTP/1.1 200 OK" in head
    assert "Content-Type: application/json" in head
    assert "Content-Length: 12" in head  # {"ok": true}
    assert "Connection: keep-alive" in head


def test_format_response_close():
    from richard.web.app import Response

    raw = _format_response(Response.text("hi"), keep_alive=False)
    head = raw.split(b"\r\n\r\n", 1)[0].decode()
    assert "Connection: close" in head


# --- end-to-end over a real socket ---


def test_serve_web_handles_http_request(tmp_path):
    from richard.web.app import _serve_request

    app = _app(tmp_path)

    async def _handle(reader, writer):
        try:
            await _serve_request(app, reader, writer)
        except Exception:
            pass
        finally:
            writer.close()

    async def run():
        server = await asyncio.start_server(_handle, "127.0.0.1", 0)
        port = server.sockets[0].getsockname()[1]
        try:
            reader, writer = await asyncio.open_connection("127.0.0.1", port)
            writer.write(b"GET /api/status HTTP/1.1\r\nHost: localhost\r\nConnection: close\r\n\r\n")
            await writer.drain()
            data = await reader.read()
            writer.close()
            await writer.wait_closed()
            return data
        finally:
            server.close()
            await server.wait_closed()

    raw = asyncio.run(asyncio.wait_for(run(), timeout=5)).decode()
    assert "HTTP/1.1 200 OK" in raw
    assert '"ok": true' in raw


def test_serve_web_keep_alive_serves_two_requests(tmp_path):
    from richard.web.app import _serve_request

    app = _app(tmp_path)

    async def _handle(reader, writer):
        try:
            await _serve_request(app, reader, writer)
        except Exception:
            pass
        finally:
            writer.close()

    async def run():
        server = await asyncio.start_server(_handle, "127.0.0.1", 0)
        port = server.sockets[0].getsockname()[1]
        try:
            reader, writer = await asyncio.open_connection("127.0.0.1", port)
            # two requests pipelined on one keep-alive connection
            req = (b"GET /api/status HTTP/1.1\r\nHost: localhost\r\nConnection: keep-alive\r\n\r\n"
                   b"GET /api/config HTTP/1.1\r\nHost: localhost\r\nConnection: close\r\n\r\n")
            writer.write(req)
            await writer.drain()
            data = await reader.read()
            writer.close()
            await writer.wait_closed()
            return data
        finally:
            server.close()
            await server.wait_closed()

    raw = asyncio.run(asyncio.wait_for(run(), timeout=5)).decode()
    assert raw.count("HTTP/1.1 200 OK") == 2
    assert '"ok": true' in raw
    assert '"llm_endpoint"' in raw

# --- system prompt (editable base character) ---


def test_get_config_exposes_system_prompt_and_default(tmp_path):
    app = _app(tmp_path)
    data = _body(app.handle("GET", "/api/config"))
    assert data["personality"]["system_prompt"] == ""
    # read-only default so the UI can show it as a placeholder
    assert "prompt_default" in data
    assert data["prompt_default"].startswith("You are {name},")


def test_put_sets_and_clears_system_prompt(tmp_path):
    app = _app(tmp_path)
    app.handle("PUT", "/api/config", json.dumps({"personality": {"system_prompt": "You are a butler."}}).encode())
    assert load_config(app._config_path).personality.system_prompt == "You are a butler."
    app.handle("PUT", "/api/config", json.dumps({"personality": {"system_prompt": ""}}).encode())
    assert load_config(app._config_path).personality.system_prompt == ""


# --- reboot serve ---


def test_restart_calls_injected_callable(tmp_path):
    calls = []
    config_path = tmp_path / "config.toml"
    save_config(Config(), config_path)
    app = WebApp(
        config_path=config_path,
        memory_store=MemoryStore(":memory:"),
        relays=RelayRegistry(),
        restart=lambda: calls.append(True),
    )
    resp = app.handle("POST", "/api/restart")
    assert resp.status == 200
    assert _body(resp)["restarting"] is True
    assert calls == [True]


def test_restart_unavailable_returns_503(tmp_path):
    app = _app(tmp_path)  # no restart callable injected
    resp = app.handle("POST", "/api/restart")
    assert resp.status == 503


# --- TTS server tuning knobs ---


def test_config_api_has_no_chatterbox_knobs(tmp_path):
    app = _app(tmp_path)
    v = _body(app.handle("GET", "/api/config"))["voice"]
    for key in ("tts_exaggeration", "tts_cfg_weight", "tts_temperature", "tts_speed"):
        assert key not in v
    resp = app.handle("PUT", "/api/config", json.dumps({"voice": {"tts_speed": 1.4}}).encode())
    assert _body(resp)["changed"] == []
    html = app.handle("GET", "/").body.decode()
    assert 'id="voice.tts_exaggeration"' not in html
    assert "Chatterbox" not in html


# --- chat (streaming) + icon ---


class _FakeEngine:
    def __init__(self, deltas):
        self._deltas = deltas

    def respond_streaming(self, conversation):
        for d in self._deltas:
            yield d


class _BrokenEngine:
    def respond_streaming(self, conversation):
        from richard.errors import BrainUnreachable
        raise BrainUnreachable()
        yield  # pragma: no cover - makes this a generator


class _FakeWriter:
    def __init__(self):
        self.chunks = []

    def write(self, b):
        self.chunks.append(b)

    async def drain(self):
        pass


def test_chat_sse_events_streams_then_done():
    from richard.web.app import _chat_sse_events
    ev = list(_chat_sse_events(_FakeEngine(["Hi", " there"]), [{"role": "user", "content": "hello"}]))
    assert ev[0] == 'data: {"delta": "Hi"}\n\n'
    assert ev[1] == 'data: {"delta": " there"}\n\n'
    assert ev[-1] == 'data: {"done": true}\n\n'


def test_chat_sse_events_brain_unreachable_yields_error_then_done():
    from richard.web.app import _chat_sse_events
    ev = list(_chat_sse_events(_BrokenEngine(), [{"role": "user", "content": "hi"}]))
    assert any('"error"' in e for e in ev)
    assert ev[-1] == 'data: {"done": true}\n\n'


def test_chat_endpoint_streams_sse(tmp_path):
    config_path = tmp_path / "config.toml"
    save_config(Config(), config_path)
    app = WebApp(
        config_path=config_path,
        memory_store=MemoryStore(":memory:"),
        relays=RelayRegistry(),
        engine_factory=lambda: _FakeEngine(["Hel", "lo"]),
    )
    from richard.web.app import _serve_chat
    w = _FakeWriter()
    asyncio.run(_serve_chat(app, b'{"messages":[{"role":"user","content":"hi"}]}', w))
    blob = b"".join(w.chunks).decode()
    assert "text/event-stream" in blob
    assert 'data: {"delta": "Hel"}' in blob
    assert 'data: {"delta": "lo"}' in blob
    assert 'data: {"done": true}' in blob


def test_chat_endpoint_503_without_engine(tmp_path):
    app = _app(tmp_path)  # no engine_factory injected
    from richard.web.app import _serve_chat
    w = _FakeWriter()
    asyncio.run(_serve_chat(app, b'{"messages":[]}', w))
    assert b"503" in b"".join(w.chunks)


def test_icon_route_returns_png(tmp_path):
    app = _app(tmp_path)
    resp = app.handle("GET", "/icon")
    assert resp.status == 200
    assert resp.content_type == "image/png"
    assert len(resp.body) > 100


def test_icon_route_keeps_particle_ring_detail(tmp_path):
    import struct

    resp = _app(tmp_path).handle("GET", "/icon")
    width, height = struct.unpack(">II", resp.body[16:24])
    assert width >= 600
    assert height >= 600


# --- voice (mic in browser → spoken reply) ---


class _FakeSynth:
    samplerate = 24000
    def synth(self, text):
        return b"\x01\x02" * 16


class _RespEngine:
    def __init__(self, reply):
        self.reply = reply
        self.seen = None
    def respond(self, conversation):
        self.seen = conversation
        return self.reply


def test_pcm_to_wav_is_valid_riff():
    from richard.web.app import _pcm_to_wav
    wav = _pcm_to_wav(b"\x00\x00" * 100, 16000)
    assert wav[:4] == b"RIFF" and wav[8:12] == b"WAVE"


def test_voice_turn_transcribes_responds_synthesizes():
    from richard.web.app import _voice_turn
    eng = _RespEngine("Ceiling fan is on.")
    out = _voice_turn(lambda b: "turn on the fan", eng, _FakeSynth(), [], b"audio")
    assert out["transcript"] == "turn on the fan"
    assert out["reply"] == "Ceiling fan is on."
    assert out["audio"].startswith("data:audio/wav;base64,")


def test_voice_turn_empty_transcript_short_circuits():
    from richard.web.app import _voice_turn
    out = _voice_turn(lambda b: "   ", _RespEngine("x"), _FakeSynth(), [], b"")
    assert out == {"transcript": "", "reply": "", "audio": None}


def test_voice_turn_brain_unreachable():
    from richard.web.app import _voice_turn
    from richard.errors import BrainUnreachable

    class Broken:
        def respond(self, conversation):
            raise BrainUnreachable()

    out = _voice_turn(lambda b: "hi", Broken(), _FakeSynth(), [], b"x")
    assert "can't reach my brain" in out["reply"].lower()
    assert out["audio"].startswith("data:audio/wav")


def test_voice_endpoint_503_without_pipeline(tmp_path):
    app = _app(tmp_path)
    from richard.web.app import _serve_voice
    w = _FakeWriter()
    asyncio.run(_serve_voice(app, b'{"messages":[],"audio":""}', w))
    assert b"503" in b"".join(w.chunks)


def test_voice_endpoint_returns_json(tmp_path):
    import base64
    config_path = tmp_path / "config.toml"
    save_config(Config(), config_path)
    app = WebApp(
        config_path=config_path,
        memory_store=MemoryStore(":memory:"), relays=RelayRegistry(),
        voice_turn=lambda messages, audio: {"transcript": "hi", "reply": "hello", "audio": "data:audio/wav;base64,AAA"},
    )
    from richard.web.app import _serve_voice
    body = ('{"messages":[],"audio":"' + base64.b64encode(b"x").decode() + '"}').encode()
    w = _FakeWriter()
    asyncio.run(_serve_voice(app, body, w))
    blob = b"".join(w.chunks).decode()
    assert "200 OK" in blob
    assert '"transcript": "hi"' in blob and '"reply": "hello"' in blob


def test_spa_has_no_activity_log():
    from richard.web.static import SPA_HTML

    for banned in ("activity-log", "command-history", "Activity log"):
        assert banned not in SPA_HTML


def test_spa_ships_realtime_voice_mode():
    from richard.web.static import SPA_HTML

    assert 'id="voice-mode"' in SPA_HTML
    assert "/v1/realtime" in SPA_HTML
    assert "input_audio_buffer.append" in SPA_HTML
    assert "response.audio.delta" in SPA_HTML
    assert "pcm16-capture" in SPA_HTML  # the AudioWorklet processor name


def test_spa_has_scheduled_loop_controls():
    from richard.web.static import SPA_HTML

    assert "loop-kind" in SPA_HTML
    assert "describeLoopSchedule" in SPA_HTML
    assert "loop-schedule-mode" in SPA_HTML


def test_config_api_round_trips_qwen_fields_and_effect(tmp_path):
    app = _app(tmp_path)
    v = _body(app.handle("GET", "/api/config"))["voice"]
    assert v["tts_model"] == "chatterbox" and v["tts_xvec_only"] is False and v["tts_effect"] == "none"
    patch = {"voice": {
        "tts_model": "", "tts_language": "Italian", "tts_instructions": "dry", "tts_xvec_only": True,
        "tts_task_type": "Base", "tts_effect": "robot", "tts_effect_strength": 70, "tts_effect_tone": 55,
    }}
    data = _body(app.handle("PUT", "/api/config", json.dumps(patch).encode()))
    assert "voice.tts_effect" in data["changed"] and "voice.tts_xvec_only" in data["changed"]
    v = load_config(app._config_path).voice
    assert (v.tts_model, v.tts_language, v.tts_instructions, v.tts_xvec_only, v.tts_task_type) == ("", "Italian", "dry", True, "Base")
    assert (v.tts_effect, v.tts_effect_strength, v.tts_effect_tone) == ("robot", 70, 55.0)


def test_put_clamps_effect_knobs_and_rejects_unknown_effect(tmp_path):
    app = _app(tmp_path)
    app.handle("PUT", "/api/config", json.dumps({"voice": {"tts_effect": "vocoder", "tts_effect_strength": 500, "tts_effect_tone": 1}}).encode())
    v = load_config(app._config_path).voice
    assert (v.tts_effect, v.tts_effect_strength, v.tts_effect_tone) == ("none", 100, 20.0)


def test_put_realtime_token(tmp_path):
    app = _app(tmp_path)
    data = _body(app.handle("PUT", "/api/config", json.dumps({"realtime": {"token": "s3cret", "port": 8767}}).encode()))
    assert "realtime.token" in data["changed"]
    rt = load_config(app._config_path).realtime
    assert (rt.token, rt.port) == ("s3cret", 8767)
    assert _body(app.handle("GET", "/api/config"))["realtime"]["token"] == "s3cret"


import base64


class FakeVoiceLibrary:
    def __init__(self, endpoint, fail=False):
        self.endpoint = endpoint
        self.fail = fail
        self.uploads = []
        self.names = ["clap1", "default"]

    def list(self):
        if self.fail:
            raise RuntimeError("server down")
        return {"voices": list(self.names), "uploaded": [{"name": "clap1", "ref_text": "oh hi", "created_at": 1}]}

    def upload(self, name, audio, filename, *, transcript="", consent=""):
        self.uploads.append((name, audio, filename, transcript, consent))
        self.names.append(name)
        return {"name": name}


def _remote_app(tmp_path, library):
    app = _app(tmp_path, voice_library_factory=lambda endpoint: library)
    config = load_config(app._config_path)
    config.voice.tts_engine = "remote"
    config.voice.tts_endpoint = "http://tts:8091"
    save_config(config, app._config_path)
    return app


def test_voices_list_needs_the_remote_engine(tmp_path):
    resp = _app(tmp_path, voice_library_factory=lambda endpoint: FakeVoiceLibrary(endpoint)).handle("GET", "/api/voices")
    assert resp.status == 503
    assert "remote" in _body(resp)["error"]


def test_voices_list_proxies_the_server(tmp_path):
    library = FakeVoiceLibrary("unused")
    data = _body(_remote_app(tmp_path, library).handle("GET", "/api/voices"))
    assert data["voices"] == ["clap1", "default"]
    assert data["uploaded"][0]["name"] == "clap1"


def test_voices_list_reports_a_dead_server(tmp_path):
    resp = _remote_app(tmp_path, FakeVoiceLibrary("unused", fail=True)).handle("GET", "/api/voices")
    assert resp.status == 503 and "server down" in _body(resp)["error"]


def test_voice_upload_forwards_the_sample_and_refreshes_the_list(tmp_path):
    library = FakeVoiceLibrary("unused")
    app = _remote_app(tmp_path, library)
    payload = {"name": "clap1v", "transcript": "oh hi", "filename": "clap1v.wav", "audio_base64": base64.b64encode(b"RIFF....").decode()}
    resp = app.handle("POST", "/api/voices", json.dumps(payload).encode())
    assert resp.status == 200, resp.body
    data = _body(resp)
    assert data["uploaded"] == "clap1v" and "clap1v" in data["voices"]
    name, audio, filename, transcript, consent = library.uploads[0]
    assert (name, audio, filename, transcript) == ("clap1v", b"RIFF....", "clap1v.wav", "oh hi")
    assert consent.startswith("web-clap1v-")


def test_voice_upload_validates_name_audio_and_size(tmp_path):
    app = _remote_app(tmp_path, FakeVoiceLibrary("unused"))
    bad_name = {"name": "bad name!", "audio_base64": base64.b64encode(b"x").decode()}
    assert app.handle("POST", "/api/voices", json.dumps(bad_name).encode()).status == 400
    no_audio = {"name": "ok", "audio_base64": ""}
    assert app.handle("POST", "/api/voices", json.dumps(no_audio).encode()).status == 400
    not_b64 = {"name": "ok", "audio_base64": "@@@"}
    assert app.handle("POST", "/api/voices", json.dumps(not_b64).encode()).status == 400
    huge = {"name": "ok", "audio_base64": base64.b64encode(b"\0" * (8 * 1024 * 1024 + 1)).decode()}
    assert app.handle("POST", "/api/voices", json.dumps(huge).encode()).status == 413


from richard.plugins.registry import PluginRecord


def _records():
    return [
        PluginRecord(name="home_assistant", version="1.0", module="richard.plugins.home_assistant:HomeAssistantPlugin", enabled=True, error="ValueError: host or token unset"),
        PluginRecord(name="reachy", version="0.1", module="richard_reachy:ReachyPlugin"),
    ]


def test_plugins_list_reports_configured_and_running_state(tmp_path):
    app = _app(tmp_path, plugin_records=_records)
    config = load_config(app._config_path)
    config.plugins.enabled = ["home_assistant"]
    save_config(config, app._config_path)
    rows = _body(app.handle("GET", "/api/plugins"))["plugins"]
    assert [(r["name"], r["configured"], r["running"]) for r in rows] == [("home_assistant", True, "error"), ("reachy", False, "disabled")]
    assert rows[0]["error"] == "ValueError: host or token unset"


def test_plugins_list_without_a_registry_discovers_installed_plugins(tmp_path):
    rows = _body(_app(tmp_path).handle("GET", "/api/plugins"))["plugins"]
    home = next(r for r in rows if r["name"] == "home_assistant")
    assert home["running"] == "unknown" and home["configured"] is False


def test_plugins_enable_and_disable_persist_and_ask_for_a_restart(tmp_path):
    app = _app(tmp_path, plugin_records=_records)
    data = _body(app.handle("PUT", "/api/plugins", json.dumps({"name": "reachy", "enabled": True}).encode()))
    assert data["restart_required"] is True
    assert load_config(app._config_path).plugins.enabled == ["reachy"]
    assert next(r for r in data["plugins"] if r["name"] == "reachy")["configured"] is True
    app.handle("PUT", "/api/plugins", json.dumps({"name": "reachy", "enabled": False}).encode())
    assert load_config(app._config_path).plugins.enabled == []


def test_plugins_enable_unknown_name_is_404_and_bad_body_is_400(tmp_path):
    app = _app(tmp_path, plugin_records=_records)
    assert app.handle("PUT", "/api/plugins", json.dumps({"name": "ghost", "enabled": True}).encode()).status == 404
    assert app.handle("PUT", "/api/plugins", b"[]").status == 400
    assert app.handle("PUT", "/api/plugins", json.dumps({"enabled": True}).encode()).status == 400


def test_web_ui_voice_page_has_sections_upload_and_effect(tmp_path):
    html = _app(tmp_path).handle("GET", "/").body.decode()
    assert 'data-panel-content="voice-stt-content voice-tts-content voice-sample-content voice-effect-content voice-turn-content voice-mic-content"' in html
    for element_id in (
        "voice.language", "voice.tts_model", "voice.tts_language", "voice.tts_instructions", "voice.tts_xvec_only",
        "voice.tts_task_type", "voice.tts_effect", "voice.tts_effect_strength", "voice.tts_effect_tone",
        "voice.endpoint_silence_ms", "voice-sample-file", "voice-sample-name", "voice-sample-transcript",
        "voice-sample-consent", "voice-sample-upload", "voice-list", "tts-voice-options",
    ):
        assert f'id="{element_id}"' in html, element_id
    for section in ("voice-stt", "voice-tts", "voice-effect", "voice-turn", "voice-mic"):
        assert f'data-save="{section}"' in html
        assert f'id="{section}-status"' in html
    assert "data-remote-only" in html
    assert "/api/voices" in html
    assert "function reflectRemoteOnly()" in html
    assert "async function uploadVoiceSample()" in html
    assert 'id="voice-content"' not in html
