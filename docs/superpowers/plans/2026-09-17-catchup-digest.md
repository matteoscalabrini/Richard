# Catch-up digest Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** When a realtime voice session starts, Richard receives one short background message describing what happened since the previous conversation ended, so he stops cold-starting and inventing the state of the house. Continuity only: the digest never starts a turn and never asks to be reported.

**Architecture:** A pure builder (`richard/catchup.py`) turns three inputs into at most ten bullet lines: perception events since the last session (from the `perception_log` table, by id), control-loop notifications since then (by id), and a live Home Assistant snapshot (lights on, people in view). A small JSON state file under `~/.richard/` remembers when the last session ended and the last seen ids. `RealtimeSession` gains `add_background(text)`: queued like perception context, drained into the first turn, but never counted as "something to say" (no wake, no `nothing_new` bypass). The CLI session factory builds the digest on a background thread and queues it; `close()` records the end. Branch `reachy-presence`, worktree `/Users/matteo/Documents/GitHub/Richard/.worktrees/reachy-presence`, HEAD 6195c23.

**Tech Stack:** Python 3.12, pytest (`.venv/bin/pytest -q`, 1002 passed at HEAD).

## Global Constraints

- The digest is persisted as a normal user-role message (it is history), placed before the user's first text; it must never be added to the pinned head.
- The digest never triggers `wake()`, never sets `_unsolicited`, and an empty client `response.create` with only a digest queued still returns the `nothing_new` short-circuit.
- Hard caps: 10 bullet lines, 600 characters total; person events individually (newest 6), noisy kinds (`scene_changed`, `stillness`, `motion_after_stillness`) collapsed to counts; notifications newest 3; lights on newest 5 names.
- The first run ever (no state file) produces no digest.
- Building the digest must not delay `session.created`: sources are read on a daemon thread with the HA call bounded by the client's timeout; any exception → no digest, one warning.
- Commit after each task with the message given. No push; deployment in Task 4.

---

### Task 1: Pure digest builder and session state file

**Files:**
- Create: `src/richard/catchup.py`
- Test: `tests/test_catchup.py`

**Interfaces:**
- `default_session_state_path() -> Path` (`~/.richard/session_state.json`).
- `class SessionState(path)`: `.read() -> dict` with keys `last_ended_at: str|None`, `last_perception_id: int`, `last_notification_id: int` (defaults None/0/0; a missing or corrupt file reads as defaults); `.write(*, last_ended_at, last_perception_id, last_notification_id) -> None` (atomic: write temp + `os.replace`, mode 0600).
- `build_digest(*, now: datetime, tz_name: str|None, last_ended_at: str|None, perception_events: list[dict], notifications: list[dict], lights_on: list[str]|None, present: list[dict]) -> str|None`. `perception_events` are `PresenceLog.recent()` dicts (`at` ISO UTC, `kind`, `subject`, `source`), already filtered to "since"; `notifications` are `ControlLoopStore.notifications()` dicts (`loop_name`, `summary`, `created_at`); `present` is `PerceptionService.presence()` (`subject`, `source`). Returns `None` when `last_ended_at is None`, or when there are no events, no notifications and nothing present and lights unknown.

Output shape (exact):
```
[catch-up] Background since the last conversation ended 2026-09-16 21:58 (11 h ago). Use it only to answer questions or ground what you say; do not report it unless asked.
- 22:14 Matteo is no longer in the camera frame (browser)
- 07:40 someone appeared in the camera frame (browser)
- camera view changed 6 times; 2 still periods
- loop "kitchen light" 07:55: the light was left on overnight
- lights on now: Lampada Scrivania, Lampade Salotto
- in view now: Matteo (browser)
```
Rules: header always first; person lines use `PerceptionEvent.line()` text for kinds `person_entered`, `person_left`, `identified`, `unknown_person`, prefixed by the local `HH:MM` from `at` (via `clock.stamp(at, tz_name)[-5:]`), newest 6 in chronological order; one collapsed line for the noisy kinds when any occurred (`camera view changed N times` / `N still periods` / `movement after stillness N times`, joined by `; `, only non-zero parts); notifications newest 3 as `loop "<loop_name>" HH:MM: <summary truncated to 80 chars>`; `lights on now: <up to 5 names>` when `lights_on` is a non-empty list, `no lights on now` when it is an empty list, nothing when `None`; `in view now: <subject (source)>, …` when `present` non-empty. Ago text: `<n> min ago` under 60 min, `<n> h ago` under 48 h, else `<n> days ago`. After assembling, drop bullets from the end until at most 10 bullets and 600 characters.

- [ ] **Step 1: Failing tests** — `tests/test_catchup.py` covering: first run → `None`; nothing since → `None`; the exact example above from fixture inputs (`now` = 2026-09-17 08:58 UTC+2 i.e. `datetime(2026, 9, 17, 6, 58, tzinfo=timezone.utc)`, `tz_name="Europe/Rome"`, `last_ended_at="2026-09-16T19:58:00+00:00"`, events with `at` `2026-09-16T20:14:00+00:00` person_left Matteo, `2026-09-17T05:40:00+00:00` person_entered "", six `scene_changed`, two `stillness`; one notification `loop_name="kitchen light"`, `summary="the light was left on overnight"`, `created_at="2026-09-17T05:55:00+00:00"`; `lights_on=["Lampada Scrivania", "Lampade Salotto"]`; `present=[{"subject": "Matteo", "source": "browser"}]`); caps (20 person events → 6 newest; 700-char inputs → ≤600 chars and ≤10 bullets); `SessionState` read defaults on missing and corrupt file, write/read round trip, file mode 0600.
- [ ] **Step 2: Run** → `ModuleNotFoundError`.
- [ ] **Step 3: Implement** `catchup.py` (pure functions + `SessionState`; use `richard.clock.stamp`/`zone_for`; `PerceptionEvent(0.0, source, kind, subject).line()` for the text).
- [ ] **Step 4: Run** tests + suite.
- [ ] **Step 5: Commit** `catchup: digest builder and session state file`

---

### Task 2: Session background message and close hook

**Files:**
- Modify: `src/richard/realtime/session.py` (`__init__` kwarg `on_close=None`; `add_background(text)`; `_drain_context`; `close()`)
- Test: `tests/test_realtime_session.py`

**Interfaces:**
- `add_background(text: str) -> None`: stores at most one pending background text (a second call replaces it); thread-safe under `_context_lock`.
- `_drain_context()` returns background text first, then the `[perception]` lines, joined by `\n`; clears both. `_has_context()` and `_has_pending_attention()` ignore the background text (so `wake()` and `create_response()`'s `nothing_new` are unaffected).
- `close()` calls `on_close()` once (exceptions logged, never raised) after the registry removal.

- [ ] **Step 1: Failing tests**: (a) `add_background("[catch-up] …")` then a spoken turn → the first user message in the brain call is exactly the background text (no `[perception]` prefix, no `Now:` prefix on it) and the user's transcript follows as its own message; (b) `add_background` then `wake()` → returns False and no turn starts; (c) `add_background` then an empty `create_response()` → `response.created` + `response.done` only, no engine call (existing `nothing_new` behaviour); (d) `on_close` called exactly once on `close()`.
- [ ] **Step 2: Run** → failures.
- [ ] **Step 3: Implement** as in Interfaces.
- [ ] **Step 4: Run** session tests + suite.
- [ ] **Step 5: Commit** `realtime: background message queued for the first turn; on_close hook`

---

### Task 3: Gather sources and wire the factory

**Files:**
- Modify: `src/richard/catchup.py` (add `class CatchUp`), `src/richard/cli.py` (`_realtime_session_factory`, `_run_serve` wiring)
- Test: `tests/test_catchup.py`, `tests/test_cli.py`

**Interfaces:**
- `CatchUp(state: SessionState, *, tz_name, perception_service=None, control_store=None, ha_client=None, now=local_now)`:
  - `.digest() -> str|None`: reads state; events = `perception_service.log.recent(since_id=state.last_perception_id, limit=200)` (skip when no service), notifications = `[n for n in control_store.notifications(limit=50) if n["id"] > state.last_notification_id]` (skip when no store), lights = names of `ha_client.list_entities()` entities with `domain == "light"` and `state == "on"` (skip → `None` on any exception or no client; name = `attributes.get("friendly_name") or entity_id`), present = `perception_service.presence()`; returns `build_digest(...)`.
  - `.mark_ended()`: writes state with `last_ended_at = now(tz).astimezone(utc).isoformat(timespec="seconds")`, `last_perception_id` = max id in `log.recent(0, 1)`… (use `log.recent(since_id=0, limit=1)`? that returns the oldest; instead query the newest id: add `PresenceLog.last_id() -> int` in `perception/events.py` (`SELECT MAX(id)`), and `ControlLoopStore.last_notification_id() -> int` similarly).
  - `.attach(session)`: starts a daemon thread that computes `digest()` and calls `session.add_background(text)` when non-empty; any exception → `log.warning("catch-up digest failed: %s", exc)`.
- `cli._run_serve`: `catchup = CatchUp(SessionState(default_session_state_path()), tz_name=config.timezone or None, perception_service=<the perception plugin's service if enabled>, control_store=control_store, ha_client=<HomeAssistantProvider.client if the plugin is enabled>)`; in `_realtime_session_factory`: `RealtimeSession(..., on_close=catchup.mark_ended)` then `catchup.attach(session)`.

- [ ] **Step 1: Failing tests**: `CatchUp.digest()` with fakes (a fake log with `recent`/`last_id`, a fake store with `notifications`/`last_notification_id`, a fake HA client with `list_entities` returning objects with `entity_id/state/attributes/domain`) produces a digest containing the expected lines; `mark_ended()` writes the state with the newest ids; `attach()` queues the digest on a fake session (join the thread with a timeout) and swallows a raising source with a warning; the HA client raising → digest still built without the lights line. `tests/test_cli.py`: the session factory passes `on_close` and attaches the catch-up (use the file's existing factory test pattern with fakes; if none fits, test `_build_catchup(config, registry, control_store)` as a small helper you add in `cli.py`).
- [ ] **Step 2: Run** → failures.
- [ ] **Step 3: Implement** (`PresenceLog.last_id`, `ControlLoopStore.last_notification_id`, `CatchUp`, cli wiring).
- [ ] **Step 4: Run** tests + suite.
- [ ] **Step 5: Commit** `catchup: gather perception, loop and Home Assistant state at session start`

---

### Task 4: Deploy and verify on CT123

- [ ] Push `reachy-presence` after the private-string grep from the memory note `richard-public-push-check` and the internal-IP grep are both empty.
- [ ] On the CT: `cd /opt/richard && git pull --ff-only && uv pip install --python .venv/bin/python -e ".[voice]" && systemctl restart richard`; open one session and close it (a typed smoke turn does this) so the state file exists; open a second session with a typed turn and confirm in the journal that `turn user:` for that turn was preceded by a `[catch-up]` user message (add a debug INFO log line `catch-up digest queued (%d chars)` in `attach` to make this visible) and that no unsolicited turn ran.
- [ ] CT changelog line, `pull-box-docs`, commit; memory update.

## Self-review
- Constraints covered: no wake (Task 2 tests b, c), caps (Task 1), first run none (Task 1), background thread (Task 3), persistence as user message before user text (Task 2 test a).
- Names consistent: `add_background`, `on_close`, `SessionState`, `CatchUp.attach/mark_ended/digest`, `PresenceLog.last_id`, `ControlLoopStore.last_notification_id`.
