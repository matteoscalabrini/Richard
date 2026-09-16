# Conversation hygiene Implementation Plan (round 2, plan A of 3)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Remove the failure modes seen in the first live session on 140dc5f: the leaked action-check sentinel, perception lines read as facts, no clock, no transcript in the journal, frozen face labels, and a thinking-effort control that needs a config edit.

**Architecture:** All server-side in the `richard` package on branch `reachy-presence` (worktree `/Users/matteo/Documents/GitHub/Richard/.worktrees/reachy-presence`). Motivating review: `reachy-lab/REVIEW-PROMPT-VISION-2026-09-16.md` section 8 (items 1, 4, 6, 7, 8, 9, 12). Plan B (tools: clock, forecast, web search) and Plan C (audio: STT language restriction, smart-turn) follow.

**Tech Stack:** Python 3.12, pytest (`.venv/bin/pytest -q` from the worktree root; 882 passed, 2 skipped, 3 deselected at HEAD 140dc5f), Node `node --test tests/browser/*.test.cjs` (32 tests) for the web client.

## Global Constraints

- The pinned system head and persisted tool rounds are the llama.cpp prompt-cache prefix. New per-turn text goes into the non-persisted observation slot (`Conversation.set_observation`, appended after history), never into the head or history.
- Every prompt string change is asserted verbatim in a test in the same task.
- Sentinels (`NOTHING_TO_RUN`, `NOTHING_TO_SAY`) are never spoken or stored.
- Commit after every task with the message given. No pushes; deployment is the last task of Plan C.
- Only the files a task names may change (plus tests that quote an old string, listed in the report).

---

### Task 1: The action nudge tolerates a sentinel prefix and requires a tool verb (item 1)

**Files:**
- Modify: `src/richard/engine.py` (`_PROMISE_RE`, `_is_nothing_to_run`, the two nudge branches in `respond` and `respond_streaming`)
- Test: `tests/test_engine.py`, `tests/test_engine_streaming.py`

**Interfaces:**
- Produces: `_promises_action(text) -> bool` (narrower), `_split_sentinel(text) -> tuple[bool, str]` returning `(True, "")` when the reply is the sentinel possibly followed by anything, else `(False, text)`.

- [ ] **Step 1: Failing tests**

Replace `test_promises_action_heuristic` in `tests/test_engine.py`:
```python
def test_promises_action_heuristic():
    from richard.engine import _promises_action

    # a first-person commitment followed by a tool-shaped verb
    assert _promises_action("I'll turn the fan off.")
    assert _promises_action("Turning it off now.")
    assert _promises_action("Let me dim the lights.")
    assert _promises_action("Let me check the lights for you.")
    assert _promises_action("I'll remember that.")
    assert _promises_action("I'm going to look it up.")
    # statements, hesitations and idioms are not promises
    assert not _promises_action("The fan is off.")
    assert not _promises_action("Done — lamp's off, confirmed at 40%.")
    assert not _promises_action("Mm, let me think.")
    assert not _promises_action("Let me see.")
    assert not _promises_action("One moment.")
    assert not _promises_action("you're testing how many languages I'll pretend to recognize")
    assert not _promises_action("I'll leave that decision to you.")
    assert not _promises_action("I will be honest with you.")


def test_split_sentinel_tolerates_trailing_prose():
    from richard.engine import _split_sentinel

    assert _split_sentinel("NOTHING_TO_RUN") == (True, "")
    assert _split_sentinel("NOTHING_TO_RUN.") == (True, "")
    assert _split_sentinel("NOTHING_TO_RUN\n\nBut I have to correct that action check.") == (True, "")
    assert _split_sentinel("Fine.") == (False, "Fine.")
    assert _split_sentinel("") == (False, "")
```
In the same file, `test_respond_nudge_sentinel_keeps_original_reply` uses "I'll leave that decision to you." as the promise; change that string to "I'll check the lights for you." so it still nudges. Add:
```python
def test_respond_sentinel_with_trailing_prose_keeps_original_reply():
    brain = FakeBrain(
        [
            Completion(content="I'll check the lights for you.", tool_calls=[]),
            Completion(content="NOTHING_TO_RUN\n\nBut I must correct that action check.", tool_calls=[]),
        ]
    )
    engine, store = _engine(brain)
    assert engine.respond(Conversation()) == "I'll check the lights for you."
```
In `tests/test_engine_streaming.py` add (using the file's `FakeBrain`, `FakeProvider`):
```python
def test_streaming_sentinel_with_trailing_prose_is_never_spoken():
    brain = FakeBrain([
        {"deltas": ["I'll check the fan for you."]},
        {"deltas": ["NOTHING_TO_RUN", "\n\nBut I must correct that action check."]},
    ])
    engine = Engine(brain, [FakeProvider()], Personality())
    convo = Conversation()
    convo.add_user("is the fan on?")
    out = "".join(engine.respond_streaming(convo))
    assert out == "I'll check the fan for you."
    assert "NOTHING_TO_RUN" not in out and "action check" not in out
```

- [ ] **Step 2: Run to see them fail**

`.venv/bin/pytest tests/test_engine.py tests/test_engine_streaming.py -q -k "promises or sentinel"` → failures on the new assertions.

- [ ] **Step 3: Implement**

In `src/richard/engine.py` replace `_PROMISE_RE` and `_is_nothing_to_run` with:
```python
# A promise is a first-person commitment followed, within three words, by a verb that
# maps to a tool: "I'll turn the fan off", "let me check the lights". Bare "I'll" no
# longer counts: on 2026-09-16 "I'll pretend to recognize" was nudged, the model replied
# with the sentinel plus prose, and both were spoken. Hesitations ("let me think",
# "one moment") never count. A false positive still costs one short extra completion.
_TOOL_VERBS = (
    r"turn|switch|set|dim|brighten|check|look|take a look|have a look|remember|forget|"
    r"search|open|close|lock|unlock|start|stop|play|pause|pull|refresh|enrol|enroll|"
    r"run|read|fetch|find|call|send|save|delete|remove"
)
_PROMISE_RE = re.compile(
    r"(?i)\b(?:i['’]?ll|i will|let me|i['’]?m going to|i am going to)\s+"
    r"(?:(?!think|see\b)\w+\s+){0,3}?(?:" + _TOOL_VERBS + r")\b"
    r"|\b(?:turning|switching|setting|dimming|starting|stopping|opening|closing|locking|unlocking)\b"
)


def _promises_action(text: str) -> bool:
    return bool(_PROMISE_RE.search(text or ""))


def _split_sentinel(text: str) -> tuple[bool, str]:
    """(True, "") when the reply starts with the sentinel, whatever follows it.

    The model sometimes answers the nudge with the sentinel and then argues with the
    nudge in prose; none of that is for the user."""
    stripped = (text or "").lstrip()
    if stripped.upper().startswith(NOTHING_TO_RUN):
        return True, ""
    return False, text or ""


def _is_nothing_to_run(text: str) -> bool:
    return _split_sentinel(text)[0]
```
`respond()` and `respond_streaming()` already call `_is_nothing_to_run`; nothing else changes there. Run the "let me see" case by hand: `(?:(?!think|see\b)\w+\s+){0,3}?` must not let "see the lights" through as "see" is excluded but "check" is a tool verb, so "let me see if I can dim them" is not a promise while "let me dim them" is. If `.venv/bin/python -c` checks disagree with the test list, adjust the negative lookahead, never the tests.

- [ ] **Step 4: Run the two test files, then the suite** → green.

- [ ] **Step 5: Commit** `engine: nudge needs a tool verb; sentinel prefix is never spoken`

---

### Task 2: Perception lines read as sensor guesses (item 4)

**Files:**
- Modify: `src/richard/perception/events.py` (`PerceptionEvent.line`, lines 36-47)
- Test: `tests/test_perception_events.py`

- [ ] **Step 1: Failing test** (append):
```python
def test_event_lines_read_as_camera_observations_not_facts():
    from richard.perception.events import PerceptionEvent

    def line(kind, subject=""):
        return PerceptionEvent(0.0, "browser", kind, subject).line()

    assert line("person_entered", "Matteo") == "Matteo appeared in the camera frame (browser)"
    assert line("person_entered") == "someone appeared in the camera frame (browser)"
    assert line("person_left", "Matteo") == "Matteo is no longer in the camera frame (browser)"
    assert line("identified", "Anna") == "face recognition guesses Anna is in frame; unverified, look to confirm (browser)"
    assert line("unknown_person") == "a person the face recognition does not know is in the camera frame (browser)"
    assert line("scene_changed") == "the camera view changed (browser)"
    assert line("motion_after_stillness") == "movement in the camera frame after a long stillness (browser)"
    assert line("stillness", "12") == "nothing has moved in the camera frame for 12 minutes (browser)"
```
- [ ] **Step 2: Run** → fails on the first assertion.
- [ ] **Step 3: Implement** by replacing the dict in `line()`:
```python
        text = {
            "person_entered": f"{who} appeared in the camera frame",
            "person_left": f"{who} is no longer in the camera frame",
            "identified": f"face recognition guesses {self.subject} is in frame; unverified, look to confirm",
            "unknown_person": "a person the face recognition does not know is in the camera frame",
            "motion_after_stillness": "movement in the camera frame after a long stillness",
            "scene_changed": "the camera view changed",
            "stillness": f"nothing has moved in the camera frame for {self.subject} minutes",
        }.get(self.kind, self.kind)
```
Also update `src/richard/realtime/visual.py`'s `_SCENE_CHANGE_RE` to `r"\bthe (?:scene|camera view) changed\b"` and its test in `tests/test_realtime_visual.py` (add a positive for "the camera view changed").
- [ ] **Step 4: Run** `tests/test_perception_events.py tests/test_realtime_visual.py tests/test_realtime_session.py tests/test_realtime_registry.py tests/test_browser_presence_backend.py` and fix any test that quoted the old lines ("entered", "recognised", "the scene changed"), then the suite.
- [ ] **Step 5: Commit** `perception: context lines are camera observations, recognition is a guess`

---

### Task 3: Transcript in the journal (item 6)

**Files:**
- Modify: `src/richard/realtime/session.py` (`_run_turn` after the transcript, `_respond` after `add_assistant`, `create_item` for typed text)
- Test: `tests/test_realtime_session.py`

- [ ] **Step 1: Failing test**:
```python
def test_turn_logs_user_text_and_reply(caplog):
    import logging

    session, emitted, done = collect_session()
    try:
        with caplog.at_level(logging.INFO, logger="richard.realtime"):
            session.feed_audio(FRAME * 2)
            wait(done)
        msgs = [r.getMessage() for r in caplog.records]
        assert any(m == "turn user: 'turn the fan on'" for m in msgs)
        assert any(m == "turn reply: 'Sure thing, the fan is on now.'" for m in msgs)
    finally:
        session.close()
```
- [ ] **Step 2: Run** → fails.
- [ ] **Step 3: Implement**: in `_run_turn`, right after `timing.mark("stt")`, add `log.info("turn user: %r", transcript.text if isinstance(transcript, Transcription) else transcript)` (place it after the `Transcription` unwrapping so it logs the string; if the transcript is empty log nothing). In `create_item`, when a message item has text, `log.info("turn user: %r", text[:300])`. In `_respond`, immediately after `self.conversation.add_assistant(full)`, add `log.info("turn reply: %r", full[:300])`. For a cancelled turn with partial text add `log.info("turn reply (cancelled): %r", full[:300])` next to the `item_truncated` emit.
- [ ] **Step 4: Run** the session tests, then the suite.
- [ ] **Step 5: Commit** `realtime: log user text and reply per turn`

---

### Task 4: Time — memory timestamps, a clock line every turn (item 9, prompt side)

**Files:**
- Create: `src/richard/clock.py`
- Modify: `src/richard/config.py` (new top-level field `timezone: str = ""`, load/save), `src/richard/providers/memory.py:31`, `src/richard/realtime/session.py` (`_respond`, the `set_observation` call), `src/richard/web/app.py` (`_chat_sse_events`, set the slot on the fresh conversation), `src/richard/cli.py` (pass `config.timezone` to the session factory and the web app)
- Test: `tests/test_clock.py` (new), `tests/test_config.py`, `tests/test_providers_memory.py` (or wherever the memory provider is tested; find with `grep -l MemoryProvider tests/`), `tests/test_browser_presence_backend.py`

**Interfaces:**
- Produces: `clock.local_now(tz_name: str | None = None) -> datetime` (tz-aware; `zoneinfo.ZoneInfo(tz_name)` when given and valid, else `datetime.now().astimezone()`), `clock.now_line(now: datetime) -> str` = `f"Now: {now:%A %Y-%m-%d %H:%M %Z}"`, `clock.stamp(iso_utc: str, tz_name: str | None) -> str` = `"%Y-%m-%d %H:%M"` in local time. `RealtimeSession(..., tz_name: str | None = None)`. Config `timezone` (e.g. "Europe/Rome"; "" = system).

- [ ] **Step 1: Failing tests**

`tests/test_clock.py`:
```python
from datetime import datetime, timezone

from richard.clock import local_now, now_line, stamp


def test_now_line_format():
    now = datetime(2026, 9, 16, 19, 31, tzinfo=timezone.utc)
    assert now_line(now) == "Now: Wednesday 2026-09-16 19:31 UTC"


def test_stamp_converts_utc_iso_to_zone():
    assert stamp("2026-09-16T17:31:00+00:00", "Europe/Rome") == "2026-09-16 19:31"
    assert stamp("garbage", "Europe/Rome") == ""


def test_local_now_uses_zone_when_valid():
    assert local_now("Europe/Rome").tzinfo is not None
    assert local_now("Not/AZone").tzinfo is not None  # falls back to system local
```
Memory provider test (append to the provider's test file): a memory added at a known UTC time renders as `- [1] 2026-09-16 19:31 — likes tea` when the provider is built with `tz_name="Europe/Rome"`. (`MemoryProvider(store, tz_name=...)`; store rows carry `created_at` ISO UTC, see `memory.py:44`.)
Session test (append to `tests/test_browser_presence_backend.py`): a typed non-visual turn's brain call has, as its last message, a user message whose first text part starts with `"Now: "`, and that message is not in `session.conversation.history()`.
Config test: `timezone = "Europe/Rome"` round-trips through `load_config`/`save_config`; default is `""`.

- [ ] **Step 2: Run** → failures.
- [ ] **Step 3: Implement**

`src/richard/clock.py`:
```python
"""Local time for prompts and tools. The system head is pinned for the prompt cache, so
the clock never goes there: callers put `now_line()` into the per-turn, non-persisted
observation slot, and memories render their stored UTC timestamp through `stamp()`."""
from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


def _zone(tz_name: str | None):
    if tz_name:
        try:
            return ZoneInfo(tz_name)
        except (ZoneInfoNotFoundError, ValueError):
            return None
    return None


def local_now(tz_name: str | None = None) -> datetime:
    zone = _zone(tz_name)
    return datetime.now(zone) if zone is not None else datetime.now().astimezone()


def now_line(now: datetime) -> str:
    return f"Now: {now:%A %Y-%m-%d %H:%M %Z}"


def stamp(iso_utc: str, tz_name: str | None = None) -> str:
    try:
        moment = datetime.fromisoformat(iso_utc)
    except (TypeError, ValueError):
        return ""
    zone = _zone(tz_name)
    local = moment.astimezone(zone) if zone is not None else moment.astimezone()
    return local.strftime("%Y-%m-%d %H:%M")
```
Memory provider: `MemoryProvider.__init__(self, store, *, tz_name: str | None = None)`; line 31 becomes `f"- [{m.id}] {stamp(m.created_at, self._tz_name)} — {m.text}"` (fall back to `f"- [{m.id}] {m.text}"` when `stamp` returns ""). Update the intro sentence to mention the date: "Each memory is prefixed with the date and time it was saved."
Config: add `timezone: str = ""` to `Config` (`config.py:169-182`), read `data.get("timezone", "")` in the loader, write it in `save_config` when non-empty.
Session: constructor kwarg `tz_name=None`; in `_respond`, replace `self.conversation.set_observation(observation)` with:
```python
            parts = [{"type": "text", "text": now_line(local_now(self._tz_name))}]
            if observation:
                parts.extend(observation)
            self.conversation.set_observation(parts)
```
Web chat: in `_chat_sse_events` (app.py ~320-334) after building the conversation, `conversation.set_observation([{"type": "text", "text": now_line(local_now(tz_name))}])`, with `tz_name` handed to `WebApp` as a constructor kwarg from `cli.py` (default None). `cli.py`: pass `tz_name=config.timezone or None` to the session factory and `MemoryProvider`.
- [ ] **Step 4: Run** the four test files, then the suite (the observation tests in `test_browser_presence_backend.py` index `brain.calls[0][-1]["content"][0]` expecting the visual instruction text; they now find the "Now:" part first, so update those assertions to look at the part whose text contains "visual observation").
- [ ] **Step 5: Commit** `time: memories carry their date, every turn carries the clock`

---

### Task 5: Re-identify faces while someone is present (item 8)

**Files:**
- Modify: `src/richard/perception/pipeline.py` (lines 108-118), `src/richard/perception/events.py` (`PresenceState.observe`, lines 77-110)
- Test: `tests/test_perception_events.py`, `tests/test_perception_pipeline.py`

**Interfaces:**
- `PresenceState.observe(ts, persons, names)` switches `subject` to a new name after two consecutive votes for it and resets the vote table; a vote for the current subject clears competing counts. Emits `identified` for the new subject.
- `PerceptionPipeline.step` runs identification whenever persons are present and `_face_every` has elapsed, not only while the subject is unknown.

- [ ] **Step 1: Failing tests**

`tests/test_perception_events.py`:
```python
def test_presence_switches_subject_after_two_consecutive_votes():
    from richard.perception.events import PresenceState
    from richard.perception.detect import Detection

    state = PresenceState("browser", enter_debounce_s=0.0, leave_debounce_s=10.0)
    box = Detection(score=0.9, box=(0.1, 0.1, 0.5, 0.5))
    kinds = []
    t = 0.0
    for name in ["Matteo", "Matteo", "Matteo", "Anna", "Matteo", "Anna", "Anna", "Anna"]:
        t += 1.0
        kinds += [(e.kind, e.subject) for e in state.observe(t, [box], [name])]
    assert ("identified", "Matteo") in kinds
    assert ("identified", "Anna") in kinds
    assert kinds.count(("identified", "Anna")) == 1          # one flip, after the two consecutive Anna votes
    assert kinds.index(("identified", "Anna")) > kinds.index(("identified", "Matteo"))
    assert state.present()[0].subject == "Anna"
```
(Check `Detection`'s real constructor in `src/richard/perception/detect.py` and adapt the fixture; other tests in the file build detections already, copy their helper.)

`tests/test_perception_pipeline.py`: extend the existing identification test (find the one that uses a fake identifier) so that the fake identifier returns "Matteo" for the first N frames and "Anna" afterwards while a person stays detected the whole time, with the pipeline clock advancing ≥ 1 s per frame; assert an `identified` event with subject "Anna" is produced without any `person_left`.

- [ ] **Step 2: Run** → the events test fails (today Anna's votes accumulate but Matteo's 3 votes keep... actually today it flips on the second Anna vote regardless of order; the assertion `count == 1` and the pipeline test are what fail). Confirm which assertions fail and note it in the report.
- [ ] **Step 3: Implement**

In `PresenceState.observe`, replace the vote block (`person.votes[name] = ...` through the `identified` emit) with:
```python
            if name:
                if name == person.subject:
                    person.votes = {}
                else:
                    person.votes = {name: person.votes.get(name, 0) + 1}
                    if person.votes[name] >= 2:
                        person.subject = name
                        person.votes = {}
                        events.append(PerceptionEvent(ts, self.source_id, "identified", name, 0.9, persons[0].box))
```
(A vote for a third name resets the count for the previous challenger: only consecutive votes for the same new name flip the label.)

In `pipeline.py:110-111`, replace:
```python
        unresolved = bool(persons) and (not present or present[0].subject == "unknown")
        if self._identifier is not None and self._settings.identity_enabled and unresolved and ts - self._last_face_at >= self._face_every:
```
with:
```python
        # Keep identifying while anyone is present: a label assigned once used to be
        # frozen until a 10 s leave, so two people swapping in front of the camera kept
        # the first name (2026-09-16). One check per second is cheap enough.
        if self._identifier is not None and self._settings.identity_enabled and persons and ts - self._last_face_at >= self._face_every:
```
- [ ] **Step 4: Run** `tests/test_perception_events.py tests/test_perception_pipeline.py`, then the suite.
- [ ] **Step 5: Commit** `perception: keep identifying while someone is present; flip on two consecutive votes`

---

### Task 6: Thinking effort as a web UI setting, applied live (item 12)

**Files:**
- Modify: `src/richard/config.py` (field `llm_thinking_effort: str = ""`, values `""|off|low|medium|high`; a helper `thinking_kwargs(level) -> dict`; load/save), `src/richard/brain/llama_cpp.py` (`set_extra_body`), `src/richard/web/app.py` (`_config_to_dict`, `_apply_config_update`, a `brain_settings` callback), `src/richard/web/static.py` (Brain section select + FIELDS entry), `src/richard/cli.py` (pass the callback)
- Test: `tests/test_config.py`, `tests/test_llama_cpp_client.py`, `tests/test_web.py`

**Interfaces:**
- `config.thinking_kwargs(level)` → `{"enable_thinking": False}` for "off", `{"enable_thinking": True, "reasoning_effort": level}` for low/medium/high, `{}` for "". On load, when `llm_thinking_effort` is set it overrides `llm_extra_body["chat_template_kwargs"]` with that dict (merged over any other keys there). `LlamaCppBrain.set_extra_body(extra: dict)` replaces `_extra_body`. `WebApp(..., apply_brain: Callable[[Config], None] | None = None)`; `_put_config` calls it after a successful save when `llm_thinking_effort` changed. `cli.py` supplies `lambda cfg: brain.set_extra_body(resolve_brain_role(cfg, "conversational").extra_body)`.

- [ ] **Step 1: Failing tests**
`tests/test_config.py`:
```python
def test_thinking_effort_overrides_chat_template_kwargs(tmp_path):
    from richard.config import load_config, save_config, thinking_kwargs, Config

    assert thinking_kwargs("off") == {"enable_thinking": False}
    assert thinking_kwargs("high") == {"enable_thinking": True, "reasoning_effort": "high"}
    assert thinking_kwargs("") == {}
    path = tmp_path / "config.toml"
    cfg = Config(llm_thinking_effort="low", llm_extra_body={"chat_template_kwargs": {"enable_thinking": False, "keep": 1}})
    save_config(cfg, path)
    loaded = load_config(path)
    assert loaded.llm_thinking_effort == "low"
    assert loaded.llm_extra_body["chat_template_kwargs"] == {"keep": 1, "enable_thinking": True, "reasoning_effort": "low"}
```
`tests/test_llama_cpp_client.py`: `set_extra_body({"a": 1})` is reflected in the next request body (copy the file's existing MockTransport pattern that inspects the JSON body).
`tests/test_web.py`: `PUT /api/config {"llm_thinking_effort": "medium"}` → `changed == ["llm_thinking_effort"]`, echoed config has it, the on-disk TOML has `llm_thinking_effort = "medium"`, and a recorded `apply_brain` callback was called once with a `Config` whose `llm_extra_body["chat_template_kwargs"]["reasoning_effort"] == "medium"`; `"turbo"` → 400.

- [ ] **Step 2: Run** → failures.
- [ ] **Step 3: Implement** per the interfaces. Static UI: in the Brain window (static.py:548-565) add
```html
<label>Thinking effort <select id="llm_thinking_effort"><option value="">config file</option><option value="off">off (fastest)</option><option value="low">low</option><option value="medium">medium</option><option value="high">high</option></select></label>
```
and a `FIELDS` entry `{id:'llm_thinking_effort', path:['llm_thinking_effort'], t:'sel', sec:'brain'}`. Validation in `_apply_config_update`: accept only the five values, else `ValueError`.
- [ ] **Step 4: Run** the three test files, the suite, and `node --test tests/browser/*.test.cjs`.
- [ ] **Step 5: Commit** `web: thinking effort setting, applied to the running brain`

---

### Task 7: Knowledge questions go to the thinking role (item 7)

**Files:**
- Create: `src/richard/routing.py`
- Modify: `src/richard/engine.py` (`Engine.__init__(brain, providers, personality, max_rounds=5, brains: dict[str, Brain] | None = None)`; pick the brain per call), `src/richard/cli.py` (`_run_serve`: build `brains["thinking"]` when `config.brains` has a `thinking` role; pass to every `Engine(...)` construction site in serve)
- Test: `tests/test_routing.py` (new), `tests/test_engine_streaming.py`

**Interfaces:**
- `routing.role_for(user_text: str | None) -> str` returns `"thinking"` for knowledge questions, else `"conversational"`. Knowledge question = starts with or contains a wh-word (EN: who|what|when|where|why|how|which|explain|tell me about; IT: chi|cosa|che cos|quando|dove|perché|come|quale|spiegami|dimmi) AND contains no household/vision/tool word (light|lamp|luce|lampada|lampade|faretti|switch|accendi|spegni|temperature|temperatura|camera|look|guarda|vedi|foto|remember|ricorda|forget|time|ore|weather|meteo|search|cerca) AND is at least 5 words.
- `Engine` uses `self._brains.get(role_for(latest user text), self._brain)` in both `respond` and `respond_streaming`; with no `brains` the behaviour is unchanged.

- [ ] **Step 1: Failing tests**
`tests/test_routing.py`:
```python
import pytest
from richard.routing import role_for

@pytest.mark.parametrize("text", [
    "who wrote Merrily We Roll Along and when did it open",
    "why does the sky look red at sunset here in Italy",
    "spiegami come funziona una pompa di calore",
    "quale musical ha scritto Sondheim nel 1970",
])
def test_knowledge_questions_go_to_thinking(text):
    assert role_for(text) == "thinking"

@pytest.mark.parametrize("text", [
    "what time is it",
    "turn the kitchen light off",
    "che ore sono",
    "what am I holding",
    "how are you",
    "quando hai visto Anna l'ultima volta",
    None, "",
])
def test_everything_else_stays_conversational(text):
    assert role_for(text) == "conversational"
```
Engine test: with `brains={"thinking": other_brain}`, a conversation whose last user text is a knowledge question streams from `other_brain`; a household request streams from the default brain.

- [ ] **Step 2: Run** → failures.
- [ ] **Step 3: Implement** `routing.py` with the two regexes and the word-count rule; engine change as in Interfaces (`_latest_user_text` already exists from the previous plan); `cli.py` builds the map only if `"thinking" in config.brains`.
- [ ] **Step 4: Run** tests + suite.
- [ ] **Step 5: Commit** `engine: knowledge questions use the thinking brain role when configured`

---

## Self-review
- Items covered: 1→A1, 4→A2, 6→A3, 9(prompt side)→A4, 8→A5, 12→A6, 7→A7. Item 9's clock tool, 10 and 11 are Plan B; 2 and 3 are Plan C; 5 is a deploy-time data step in Plan C.
- Names used across tasks: `_latest_user_text` (exists), `set_observation` (exists), `now_line/local_now/stamp` (A4, used by B1), `thinking_kwargs` (A6), `role_for` (A7).
