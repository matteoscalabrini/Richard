# Vision pressure removal + turn timing Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Stop Richard from being pushed toward "taking a look" on every voice turn, and add per-turn latency marks so the pipeline can be measured.

**Architecture:** All changes are server-side in the `richard` package on branch `reachy-presence` (worktree `/Users/matteo/Documents/GitHub/Richard/.worktrees/reachy-presence`). The browser client and the Reachy app are untouched: the server decides per turn whether an ambient frame is attached, rewrites the client camera description, gates face tools, prunes old images, and logs timing. The review that motivates every task is `reachy-lab/REVIEW-PROMPT-VISION-2026-09-16.md` (proposal numbers below refer to its section 7).

**Tech Stack:** Python 3.12, pytest (`.venv/bin/pytest` in the worktree), no new dependencies.

## Global Constraints

- Run tests from the worktree root: `cd /Users/matteo/Documents/GitHub/Richard/.worktrees/reachy-presence && .venv/bin/pytest -q`. The full suite must stay green (843 passed, 2 skipped, 3 deselected at HEAD b6a2fd4; `-m "not gpu"` style deselects are configured in `pyproject.toml`).
- The pinned system head and the persisted tool rounds are the llama.cpp prompt-cache prefix. Do not change how history is replayed except where a task says so (Task 6 prune, Task 4 conditional tools), and document the cache cost there.
- Prompt strings are quoted verbatim in tests; when a task changes a string, it changes the test in the same task.
- Commit after every task with the message given. No pushes; deployment is Task 8 and is done by hand with the commands listed.
- English only in code, comments and prompts, except the Italian cue/regex words that are data.

---

### Task 1: Per-turn timing marks (proposal 9)

**Files:**
- Create: `src/richard/realtime/timing.py`
- Modify: `src/richard/realtime/session.py` (`_handle_frame` utterance branch ~line 350, `create_response` ~line 237, `_run_turn` after `get_transcript()` ~line 435, `_respond` first delta ~line 589, `_speak` after `audio_delta` ~line 691, and the `response_done` emit ~line 667)
- Test: `tests/test_realtime_timing.py` (new), `tests/test_realtime_session.py` (one new test)

**Interfaces:**
- Produces: `class TurnTiming` with `start()`, `mark(name: str)`, `summary() -> dict[str, int]` (milliseconds since start, only marks that were set), `line() -> str` (log line `turn timing: stt=412ms first_token=830ms first_audio=1310ms`). Session attribute `self._timing: TurnTiming`.

- [ ] **Step 1: Write the failing unit test for TurnTiming**

`tests/test_realtime_timing.py`:
```python
from richard.realtime.timing import TurnTiming


def test_marks_are_milliseconds_since_start_and_only_set_marks_are_reported():
    clock = iter([10.0, 10.4, 10.9, 11.3])
    timing = TurnTiming(now=lambda: next(clock))
    timing.start()
    timing.mark("stt")
    timing.mark("first_token")
    timing.mark("first_audio")
    assert timing.summary() == {"stt": 400, "first_token": 900, "first_audio": 1300}
    assert timing.line() == "turn timing: stt=400ms first_token=900ms first_audio=1300ms"


def test_first_mark_wins_and_marks_before_start_are_ignored():
    clock = iter([5.0, 5.1, 5.2])
    timing = TurnTiming(now=lambda: next(clock))
    timing.mark("first_token")  # no start yet: dropped
    timing.start()
    timing.mark("first_token")
    timing.mark("first_token")  # second call must not overwrite the first
    assert timing.summary() == {"first_token": 100}


def test_line_without_start_is_empty():
    assert TurnTiming().line() == ""
```

- [ ] **Step 2: Run it to verify it fails**

Run: `.venv/bin/pytest tests/test_realtime_timing.py -q`
Expected: FAIL with `ModuleNotFoundError: No module named 'richard.realtime.timing'`

- [ ] **Step 3: Implement TurnTiming**

`src/richard/realtime/timing.py`:
```python
"""Per-turn latency marks for the realtime session.

One TurnTiming lives per turn. start() is called when the user stops speaking (or
when a typed turn is requested); marks record the first time each stage is reached.
The summary is logged once per turn so the pipeline can be measured from the log
instead of guessed. Marks set before start() or set twice are ignored on purpose:
the first token is the number that matters, not the last.
"""
from __future__ import annotations

import time
from collections.abc import Callable

ORDER = ("stt", "first_token", "first_audio")


class TurnTiming:
    def __init__(self, now: Callable[[], float] = time.monotonic) -> None:
        self._now = now
        self._t0: float | None = None
        self._marks: dict[str, float] = {}

    def start(self) -> None:
        self._t0 = self._now()
        self._marks = {}

    def mark(self, name: str) -> None:
        if self._t0 is None or name in self._marks:
            return
        self._marks[name] = self._now()

    def summary(self) -> dict[str, int]:
        if self._t0 is None:
            return {}
        ordered = [n for n in ORDER if n in self._marks] + [
            n for n in self._marks if n not in ORDER
        ]
        return {n: int(round((self._marks[n] - self._t0) * 1000)) for n in ordered}

    def line(self) -> str:
        summary = self.summary()
        if not summary:
            return ""
        return "turn timing: " + " ".join(f"{k}={v}ms" for k, v in summary.items())
```

- [ ] **Step 4: Run the unit test to verify it passes**

Run: `.venv/bin/pytest tests/test_realtime_timing.py -q`
Expected: 3 passed

- [ ] **Step 5: Write the failing session test**

Append to `tests/test_realtime_session.py` (uses the file's `collect_session`, `wait`, `FRAME`):
```python
def test_turn_logs_timing_marks(caplog):
    import logging

    session, emitted, done = collect_session()
    try:
        with caplog.at_level(logging.INFO, logger="richard.realtime"):
            session.feed_audio(FRAME * 2)
            wait(done)
        lines = [r.getMessage() for r in caplog.records if r.getMessage().startswith("turn timing:")]
        assert len(lines) == 1
        assert "stt=" in lines[0] and "first_token=" in lines[0] and "first_audio=" in lines[0]
    finally:
        session.close()
```

- [ ] **Step 6: Run it to verify it fails**

Run: `.venv/bin/pytest tests/test_realtime_session.py::test_turn_logs_timing_marks -q`
Expected: FAIL, `assert 0 == 1` (no timing line logged)

- [ ] **Step 7: Wire the marks into the session**

In `src/richard/realtime/session.py`:

Import: `from richard.realtime.timing import TurnTiming`

In `__init__`, after `self._input_item_id = ""`:
```python
        self._timing = TurnTiming()
```

In `_handle_frame`, the `utterance` branch, before `self._emit(events.speech_stopped())`:
```python
                self._timing.start()
```

In `create_response` (the method that ends with `self._start_turn(None)` at ~line 237), right before that final `self._start_turn(None)`:
```python
        self._timing.start()
```

In `_run_turn`, right after `transcript = get_transcript()` succeeds (before the `except`), add:
```python
            self._timing.mark("stt")
```
(Put it as the last line inside the `try:` block.)

In `_respond`, inside the `for delta in self._engine.respond_streaming(...)` loop, as the first statement of the loop body:
```python
                self._timing.mark("first_token")
```

In `_speak`, immediately after `self._emit(events.audio_delta(response_id, pcm))`:
```python
            self._timing.mark("first_audio")
```

In `_respond`, right before `self._emit(events.response_done(response_id, status))`:
```python
        line = self._timing.line()
        if line:
            log.info("%s status=%s", line, status)
```

- [ ] **Step 8: Run the session test and the full suite**

Run: `.venv/bin/pytest tests/test_realtime_session.py::test_turn_logs_timing_marks tests/test_realtime_timing.py -q` then `.venv/bin/pytest -q`
Expected: all pass (the caplog assertion uses `startswith("turn timing:")`, so the appended ` status=completed` is fine)

- [ ] **Step 9: Commit**

```bash
git add src/richard/realtime/timing.py src/richard/realtime/session.py tests/test_realtime_timing.py tests/test_realtime_session.py
git commit -m "realtime: log per-turn timing marks (stt, first token, first audio)"
```

---

### Task 2: Ambient frame only on visual turns (proposals 1 and 2)

**Files:**
- Create: `src/richard/realtime/visual.py`
- Modify: `src/richard/realtime/session.py` (`_respond`, the block around `if not self.visual_context or self.source_id != observed_source:` ~line 526)
- Test: `tests/test_realtime_visual.py` (new), `tests/test_browser_presence_backend.py` (one new test next to `test_session_replaces_or_clears_source_observation_without_persisting_it`)

**Interfaces:**
- Produces: `visual.wants_frame(user_text: str | None, *, unsolicited: bool, context: str | None) -> bool`.
- The `visual_context` session flag keeps its wire meaning ("a frame source is available for this session"); the server now attaches the frame only when `wants_frame` is true. Consequence: the engine's `vision` phase (and the client's "let me take a look" cue) only occurs on turns that actually carry a fresh image, which fixes proposal 2 without touching the client.

- [ ] **Step 1: Write the failing test for wants_frame**

`tests/test_realtime_visual.py`:
```python
import pytest

from richard.realtime.visual import wants_frame


@pytest.mark.parametrize("text", [
    "what am I holding",
    "Look at this.",
    "can you see the cup?",
    "describe the room",
    "what colour is my shirt",
    "guarda qui",
    "cosa vedi?",
    "dai un'occhiata alla scrivania",
    "che cosa ho in mano",
    "com'è la mia maglietta oggi",
    "fai una foto",
    "take a picture",
])
def test_visual_requests_want_a_frame(text):
    assert wants_frame(text, unsolicited=False, context=None)


@pytest.mark.parametrize("text", [
    "what time is it",
    "turn the kitchen light off",
    "che ore sono",
    "raccontami una barzelletta",
    "I see what you mean, let's move on",   # idiom, not a request to look
    "see you later",
])
def test_ordinary_turns_do_not_want_a_frame(text):
    assert not wants_frame(text, unsolicited=False, context=None)


def test_unsolicited_scene_change_wants_a_frame_but_arrivals_do_not():
    assert wants_frame(None, unsolicited=True, context="[perception] 10:02 the scene changed (browser)")
    assert not wants_frame(None, unsolicited=True, context="[perception] 10:02 matteo entered (browser)")
    assert not wants_frame(None, unsolicited=False, context=None)
```

- [ ] **Step 2: Run it to verify it fails**

Run: `.venv/bin/pytest tests/test_realtime_visual.py -q`
Expected: FAIL with `ModuleNotFoundError: No module named 'richard.realtime.visual'`

- [ ] **Step 3: Implement wants_frame**

`src/richard/realtime/visual.py`:
```python
"""Decide whether a turn is visual, i.e. whether the ambient camera frame is attached.

Before 2026-09-16 every turn of a client with video carried a fresh frame plus an
instruction to use it, which pulled the model toward describing scenes and made the
client speak the "let me take a look" cue on ordinary turns. A frame now rides only
when the user's words ask for eyes, or when Richard was woken by a scene change.
Camera tool calls are unaffected: the model can still decide to look on any turn.
"""
from __future__ import annotations

import re

# Verbs and nouns that ask for eyes, English and Italian. Word-bounded and
# case-insensitive. "see" alone is excluded: "I see", "see you" are not requests.
_VISUAL_RE = re.compile(
    r"(?i)(?:"
    r"\blook(?:s|ed|ing)?\b(?! forward)|"
    r"\bwatch(?:ing)?\b|"
    r"\bcan you see\b|\bdo you see\b|\bwhat do you see\b|\bsee (?:the|this|that|my|what)\b|"
    r"\bdescribe\b|\bpicture\b|\bphoto\b|\bcamera\b|\bwebcam\b|"
    r"\bholding\b|\bwearing\b|\bcolou?r\b|\bshow you\b|"
    r"\bguard(?:a|i|are|ate)\b|\bved(?:i|ere|ete)\b|\bcosa vedi\b|\bocchiata\b|"
    r"\bfoto\b|\bdescriv(?:i|ere|imi)\b|\bin mano\b|\bindoss(?:o|i|a)\b|"
    r"\bmaglietta\b|\bcamicia\b|\bcolore\b|\bmostr(?:o|a|arti)\b"
    r")"
)

_SCENE_CHANGE_RE = re.compile(r"\bthe scene changed\b")


def wants_frame(user_text: str | None, *, unsolicited: bool, context: str | None) -> bool:
    if user_text and _VISUAL_RE.search(user_text):
        return True
    if unsolicited and context and _SCENE_CHANGE_RE.search(context):
        return True
    return False
```

- [ ] **Step 4: Run the unit test to verify it passes**

Run: `.venv/bin/pytest tests/test_realtime_visual.py -q`
Expected: all pass. If an Italian or English sample fails, extend the regex; do not weaken the negative cases.

- [ ] **Step 5: Write the failing session test**

Append to `tests/test_browser_presence_backend.py` (uses the file's `make_session`, `ScriptBrain`, `NoTools`, `wait_until`):
```python
def test_session_attaches_the_ambient_frame_only_on_visual_turns():
    shots = [
        (b"jpeg", {"source": "browser-alpha", "age_s": 0.1, "width": 1, "height": 1}),
        (b"jpeg", {"source": "browser-alpha", "age_s": 0.1, "width": 1, "height": 1}),
    ]
    brain = ScriptBrain([{"deltas": ["Ten past."]}, {"deltas": ["A cup."]}])
    session, emitted = make_session(
        Engine(brain, [NoTools()], Personality()),
        observation=lambda source_id: shots.pop(0),
    )
    try:
        session.update({"source_id": "browser-alpha", "visual_context": True})
        session.create_item({"kind": "message", "content": "what time is it"})
        session.create_response()
        wait_until(lambda: len([e for e in emitted if e["type"] == "response.done"]) == 1)
        session.create_item({"kind": "message", "content": "what am I holding"})
        session.create_response()
        wait_until(lambda: len([e for e in emitted if e["type"] == "response.done"]) == 2)

        def has_image(messages):
            return any(
                isinstance(m.get("content"), list)
                and any(p.get("type") == "image_url" for p in m["content"] if isinstance(p, dict))
                for m in messages
            )

        assert not has_image(brain.calls[0])
        assert has_image(brain.calls[1])
        phases = [e["phase"] for e in emitted if e["type"] == "response.activity"]
        assert phases[0] == "thinking"           # first turn: no vision cue
        assert "vision" in phases[1:]            # second turn: a real look
        assert len(shots) == 1                   # the frame was fetched once, not twice
    finally:
        session.close()
```

- [ ] **Step 6: Run it to verify it fails**

Run: `.venv/bin/pytest tests/test_browser_presence_backend.py::test_session_attaches_the_ambient_frame_only_on_visual_turns -q`
Expected: FAIL at `assert not has_image(brain.calls[0])`

- [ ] **Step 7: Gate the observation in the session**

In `src/richard/realtime/session.py`:

Import: `from richard.realtime.visual import wants_frame`

In `_respond`, the observation is currently fetched before the lock (`observed_source, observation = self._current_observation()`). Move the fetch after the decision so a non-visual turn never grabs a frame. Replace:
```python
        observed_source, observation = self._current_observation()
```
with:
```python
        observed_source, observation = None, None
```
and inside the locked block, replace:
```python
            if not self.visual_context or self.source_id != observed_source:
                observation = None
            self.conversation.set_observation(observation)
```
with:
```python
            visual_turn = wants_frame(user_text, unsolicited=unsolicited, context=context)
        if visual_turn:
            observed_source, observation = self._current_observation()
        with self._state_lock:
            if (token is not self._active_token or cancel.is_set()
                    or self._closed.is_set()):
                return
            if not self.visual_context or self.source_id != observed_source:
                observation = None
            self.conversation.set_observation(observation)
```
Note the lock is released and re-acquired around the frame fetch, exactly as the original code fetched the frame outside the lock. Everything that followed `set_observation` in the original locked block (`_logical_turn_id`, `response_id`, `_remember_response`, the `response_created` emit) stays inside this second locked block. `context` is the variable already assigned by `context = self._drain_context()` a few lines above; it is `None` when nothing was queued.

- [ ] **Step 8: Run the new test, then the two existing observation tests, then the suite**

Run: `.venv/bin/pytest tests/test_browser_presence_backend.py -q` then `.venv/bin/pytest -q`
Expected: all pass. `test_session_replaces_or_clears_source_observation_without_persisting_it` sends "hello" and "again", which are not visual, so it must be updated in this step: change its two texts to `("look at this", "look again")` so the first turn still carries the frame and the second (shot `None`) clears it. Keep its other assertions unchanged.

- [ ] **Step 9: Commit**

```bash
git add src/richard/realtime/visual.py src/richard/realtime/session.py tests/test_realtime_visual.py tests/test_browser_presence_backend.py
git commit -m "realtime: attach the ambient frame only on visual turns"
```

---

### Task 3: One short vision block; curiosity out of the persona (proposal 3)

**Files:**
- Modify: `src/richard/persona.py` (BASE_CHARACTER lines 11-15, PERCEPTION_RULES lines 34-44)
- Modify: `src/richard/plugins/perception/__init__.py` (CONTEXT lines 6-7)
- Modify: `src/richard/realtime/session.py` (UNSOLICITED_RULE lines 35-41)
- Test: `tests/test_persona.py`, `tests/test_plugin_perception.py`, `tests/test_realtime_session.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_persona.py`:
```python
def test_persona_does_not_tell_richard_to_look_around():
    from richard.persona import BASE_CHARACTER, PERCEPTION_RULES

    assert "look more closely" not in BASE_CHARACTER
    assert "curiosity" not in BASE_CHARACTER
    # Three sentences, no repetition of the "describe" rule.
    sentences = [s for s in PERCEPTION_RULES.split(". ") if s.strip()]
    assert len(sentences) == 3
    assert PERCEPTION_RULES.count("describe") == 1
```

Append to `tests/test_plugin_perception.py`:
```python
def test_perception_context_line_does_not_advertise_the_camera():
    from richard.plugins.perception import CONTEXT

    assert "camera" not in CONTEXT.lower()
    assert "arrives" in CONTEXT
```

Append to `tests/test_realtime_session.py`:
```python
def test_unsolicited_rule_scopes_curiosity_to_unprompted_turns():
    from richard.realtime.session import UNSOLICITED_RULE

    assert "genuinely interests you" in UNSOLICITED_RULE
    assert "NOTHING_TO_SAY" in UNSOLICITED_RULE
```

- [ ] **Step 2: Run them to verify they fail**

Run: `.venv/bin/pytest tests/test_persona.py::test_persona_does_not_tell_richard_to_look_around tests/test_plugin_perception.py::test_perception_context_line_does_not_advertise_the_camera tests/test_realtime_session.py::test_unsolicited_rule_scopes_curiosity_to_unprompted_turns -q`
Expected: 3 failed

- [ ] **Step 3: Rewrite the strings**

`src/richard/persona.py`, BASE_CHARACTER second paragraph becomes:
```python
    "Let available memories and observations shape what you notice and say. You may offer an "
    "observation, ask a relevant question, or return to a shared topic without waiting to be "
    "asked. Save meaningful new facts with the memory tool, avoiding duplicates and keeping "
    "uncertainty explicit.\n\n"
```

`src/richard/persona.py`, PERCEPTION_RULES becomes:
```python
# Static on purpose: the head is pinned per conversation for prefix caching, so what
# Richard can see is phrased conditionally instead of varying with the session. Three
# sentences: the camera tool's own description says when to look.
PERCEPTION_RULES = (
    "Perception: you see only through images attached to a message or taken with a camera "
    "tool when one is offered, so without one say you cannot see right now and never describe "
    "a scene you have not been shown. Use an image as evidence to answer the question asked or "
    "choose an action. Describe a scene or list what is in view only when asked for a description."
)
```

`src/richard/plugins/perception/__init__.py`:
```python
CONTEXT = "Ambient perception is on: you are told when someone arrives, leaves or is recognised."
```

`src/richard/realtime/session.py`, UNSOLICITED_RULE becomes:
```python
UNSOLICITED_RULE = (
    "Nobody addressed you; this is something you noticed. Consider it alongside the available "
    "conversation and memories. If something genuinely interests you, you may look more closely "
    "with an available tool, make an observation, ask a relevant question, or pick up a shared "
    "topic. Speaking is optional; avoid repeated greetings or questions. If speaking now would be "
    f"unwelcome or you have nothing worth saying, reply exactly {NOTHING_TO_SAY}."
)
```

- [ ] **Step 4: Run the persona tests and fix the ones that quote old text**

Run: `.venv/bin/pytest tests/test_persona.py tests/test_plugin_perception.py tests/test_realtime_session.py -q`
Expected: `test_prompt_ends_with_static_perception_rules` still passes ("cannot see right now" and "never describe a scene you have not been shown" are kept verbatim). If any other test quotes the removed sentence ("look more closely", "curiosity need not"), update that assertion to the new text. Then `.venv/bin/pytest -q` must be green.

- [ ] **Step 5: Commit**

```bash
git add src/richard/persona.py src/richard/plugins/perception/__init__.py src/richard/realtime/session.py tests/test_persona.py tests/test_plugin_perception.py tests/test_realtime_session.py
git commit -m "persona: one three-sentence vision rule; curiosity scoped to unsolicited turns"
```

---

### Task 4: Face tools only when faces are the topic (proposal 4)

**Files:**
- Modify: `src/richard/perception/camera.py` (`PresenceProvider.schemas` line 105-106)
- Modify: `src/richard/engine.py` (`tool_names`, `_execute`, `respond`, `respond_streaming`)
- Test: `tests/test_perception_camera.py`, `tests/test_engine_streaming.py`

**Interfaces:**
- Produces: providers MAY define `conditional_schemas(user_text: str | None) -> list[dict]` in addition to `schemas()`. The engine serves `schemas() + conditional_schemas(latest user text)` and resolves execution against `schemas() + conditional_schemas(None, all=True)`; to keep that simple, providers that use the hook also define `all_schemas() -> list[dict]` returning every schema they can ever serve. Cache note: the tool list is part of the chat template's prefix, so a turn where the face tools appear costs one re-prefill (about 2 s measured 2026-09-09) and the next turn without them costs another. This is accepted because face enrolment is rare; the always-on list shrinks from five camera-shaped tools to two.

- [ ] **Step 1: Write the failing provider test**

Append to `tests/test_perception_camera.py`:
```python
def test_presence_provider_serves_face_tools_only_when_faces_are_the_topic():
    provider = PresenceProvider(FakeService())
    assert [s["function"]["name"] for s in provider.schemas()] == ["who_is_here"]
    assert [s["function"]["name"] for s in provider.all_schemas()] == [
        "who_is_here", "last_seen", "enrol_face", "forget_face",
    ]
    assert provider.conditional_schemas("what time is it") == []
    assert provider.conditional_schemas(None) == []
    for text in ("remember my face, I'm Matteo", "forget Anna's face", "when did you last see Luca",
                 "ricordati la mia faccia", "quando hai visto Anna l'ultima volta", "riconoscimi"):
        names = [s["function"]["name"] for s in provider.conditional_schemas(text)]
        assert names == ["last_seen", "enrol_face", "forget_face"], text
```

- [ ] **Step 2: Run it to verify it fails**

Run: `.venv/bin/pytest tests/test_perception_camera.py::test_presence_provider_serves_face_tools_only_when_faces_are_the_topic -q`
Expected: FAIL, `assert [...] == ["who_is_here"]` (four names served)

- [ ] **Step 3: Implement the conditional schemas**

In `src/richard/perception/camera.py`, add after `FORGET_SCHEMA`:
```python
import re

# Face and recognition talk, English and Italian: only then are the face tools offered.
# Serving them on every turn made five of the tool list camera-shaped (review 2026-09-16).
_FACE_TOPIC_RE = re.compile(
    r"(?i)\b(?:face|faces|recogni[sz]e|recogni[sz]ed|enrol|enroll|last (?:saw|seen)|"
    r"last time you saw|faccia|viso|riconosc\w*|ricordati (?:di|la) me|"
    r"l'ultima volta|ultima volta)\b"
)
```
and replace `PresenceProvider.schemas`:
```python
    def schemas(self) -> list[dict]:
        return [WHO_SCHEMA]

    def all_schemas(self) -> list[dict]:
        return [WHO_SCHEMA, LAST_SEEN_SCHEMA, ENROL_SCHEMA, FORGET_SCHEMA]

    def conditional_schemas(self, user_text: str | None) -> list[dict]:
        if user_text and _FACE_TOPIC_RE.search(user_text):
            return [LAST_SEEN_SCHEMA, ENROL_SCHEMA, FORGET_SCHEMA]
        return []
```

- [ ] **Step 4: Run the provider test**

Run: `.venv/bin/pytest tests/test_perception_camera.py -q`
Expected: all pass (`test_presence_tools` and `test_enrol_and_forget_face_tools` call `execute` directly and are unaffected).

- [ ] **Step 5: Write the failing engine test**

Append to `tests/test_engine_streaming.py`:
```python
class FaceProvider:
    def __init__(self):
        self.executed = []

    def schemas(self):
        return [{"type": "function", "function": {"name": "who_is_here", "parameters": {}}}]

    def all_schemas(self):
        return self.schemas() + [{"type": "function", "function": {"name": "enrol_face", "parameters": {}}}]

    def conditional_schemas(self, user_text):
        return self.all_schemas()[1:] if user_text and "face" in user_text else []

    def execute(self, name, arguments):
        self.executed.append(name)
        return "ok"

    def context(self):
        return None


def test_conditional_tools_are_served_on_topic_and_still_executable():
    provider = FaceProvider()
    brain = FakeBrain([
        {"deltas": ["Nobody new."]},
        {"tool_calls": [ToolCall(id="1", name="enrol_face", arguments={"name": "Matteo"})]},
        {"deltas": ["Done."]},
    ])
    engine = Engine(brain, [provider], Personality())
    convo = Conversation()
    convo.add_user("what time is it")
    assert "".join(engine.respond_streaming(convo)) == "Nobody new."
    assert [t["function"]["name"] for t in brain.tools[0]] == ["who_is_here"]
    convo.add_assistant("Nobody new.")
    convo.add_user("remember my face")
    assert "".join(engine.respond_streaming(convo)) == "Done."
    assert [t["function"]["name"] for t in brain.tools[1]] == ["who_is_here", "enrol_face"]
    assert provider.executed == ["enrol_face"]
    assert "enrol_face" in engine.tool_names()
```

- [ ] **Step 6: Run it to verify it fails**

Run: `.venv/bin/pytest tests/test_engine_streaming.py::test_conditional_tools_are_served_on_topic_and_still_executable -q`
Expected: FAIL at the second `brain.tools[1]` assertion or with `Unknown tool: enrol_face.`

- [ ] **Step 7: Teach the engine about conditional schemas**

In `src/richard/engine.py`:

Add two helpers after `_has_image`:
```python
def _all_schemas(provider) -> list[dict]:
    """Every schema a provider can ever serve (execution and name reservation)."""
    getter = getattr(provider, "all_schemas", None)
    return getter() if callable(getter) else provider.schemas()


def _served_schemas(provider, user_text: str | None) -> list[dict]:
    """The schemas served on this turn: the always-on ones plus the on-topic extras."""
    extra = getattr(provider, "conditional_schemas", None)
    return provider.schemas() + (extra(user_text) if callable(extra) else [])


def _latest_user_text(conversation: Conversation) -> str | None:
    for message in reversed(conversation.history()):
        if message.role == "user":
            text = message.text()
            return text or None
    return None
```

Change `tool_names`:
```python
    def tool_names(self) -> list[str]:
        """Names of the tools Richard's providers own (client tools with these names are dropped)."""
        return [s["function"]["name"] for provider in self._providers for s in _all_schemas(provider)]
```

Change `_execute`'s lookup line to:
```python
            if any(s["function"]["name"] == name for s in _all_schemas(provider)):
```

In `respond`, replace the schemas line with:
```python
        user_text = _latest_user_text(conversation)
        schemas = [s for provider in self._providers for s in _served_schemas(provider, user_text)]
```

In `respond_streaming`, replace:
```python
        schemas = [s for provider in self._providers for s in provider.schemas()] + client_schemas
```
with:
```python
        user_text = _latest_user_text(conversation)
        schemas = [
            s for provider in self._providers for s in _served_schemas(provider, user_text)
        ] + client_schemas
```

- [ ] **Step 8: Run the engine tests and the suite**

Run: `.venv/bin/pytest tests/test_engine_streaming.py tests/test_engine.py tests/test_perception_camera.py -q` then `.venv/bin/pytest -q`
Expected: all pass

- [ ] **Step 9: Commit**

```bash
git add src/richard/perception/camera.py src/richard/engine.py tests/test_perception_camera.py tests/test_engine_streaming.py
git commit -m "perception: serve face tools only when faces are the topic"
```

---

### Task 5: Richard's own description for any client camera tool (proposal 5)

**Files:**
- Modify: `src/richard/realtime/events.py` (`tools_to_schemas`)
- Test: `tests/test_realtime_events.py`

**Interfaces:**
- Produces: `events.CLIENT_CAMERA_DESCRIPTION` (str). `tools_to_schemas` replaces the description of a client tool named `camera` with it; parameters stay the client's.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_realtime_events.py`:
```python
def test_client_camera_tool_gets_richards_description_but_keeps_its_parameters():
    stock = {
        "type": "function", "name": "camera",
        "description": "If the user asks you to look without saying at what, call this tool and describe what you see.",
        "parameters": {"type": "object", "properties": {"question": {"type": "string"}}, "required": ["question"]},
    }
    schemas, dropped = events.tools_to_schemas([stock], reserved=set())
    fn = schemas[0]["function"]
    assert fn["description"] == events.CLIENT_CAMERA_DESCRIPTION
    assert "describe what you see" not in fn["description"]
    assert fn["parameters"] == stock["parameters"]
    assert dropped == []
    # other client tools are passed through verbatim
    (other,), _ = events.tools_to_schemas([{"name": "move_head", "description": "Move the head."}], reserved=set())
    assert other["function"]["description"] == "Move the head."
```

- [ ] **Step 2: Run it to verify it fails**

Run: `.venv/bin/pytest tests/test_realtime_events.py::test_client_camera_tool_gets_richards_description_but_keeps_its_parameters -q`
Expected: FAIL, `AttributeError: module ... has no attribute 'CLIENT_CAMERA_DESCRIPTION'`

- [ ] **Step 3: Implement**

In `src/richard/realtime/events.py`, add after `ACTIVE_RESPONSE_CODE`:
```python
# A client-owned camera (the Reachy app, the browser) is served with Richard's own
# description, whatever the client wrote. The stock Reachy text ends with "call this
# tool and describe what you see", which contradicts the perception rule and pushes
# the model into scene narration (review 2026-09-16). Parameters stay the client's.
CLIENT_CAMERA_DESCRIPTION = (
    "Take a picture with the camera to answer a visual question or investigate something you "
    "noticed. Use the image as evidence for your response or next action. A request to look is "
    "not a request to describe the scene; describe it only when asked for a description. Answer "
    "specific visual questions directly. Each call captures the current moment."
)
```
In `tools_to_schemas`, replace the `"description": str(tool.get("description") or ""),` line with:
```python
            "description": (
                CLIENT_CAMERA_DESCRIPTION if name == "camera"
                else str(tool.get("description") or "")
            ),
```

- [ ] **Step 4: Run the events tests and the suite**

Run: `.venv/bin/pytest tests/test_realtime_events.py -q` then `.venv/bin/pytest -q`
Expected: `test_tools_to_schemas_converts_flat_specs_and_drops_reserved` now fails because it expects description "look" for `camera`; change that expected value to `events.CLIENT_CAMERA_DESCRIPTION`. Then everything passes.

- [ ] **Step 5: Commit**

```bash
git add src/richard/realtime/events.py tests/test_realtime_events.py
git commit -m "realtime: serve client camera tools with Richard's own description"
```

---

### Task 6: Prune old images from history (proposal 6)

**Files:**
- Modify: `src/richard/conversation.py` (new method `prune_images`)
- Modify: `src/richard/realtime/session.py` (`_respond`, after `self.conversation.add_assistant(full)` ~line 650)
- Test: `tests/test_conversation.py`, `tests/test_browser_presence_backend.py`

**Interfaces:**
- Produces: `Conversation.prune_images(keep: int = 2) -> int`: keeps the image parts of the last `keep` user messages that carry images; older image parts are removed and replaced by a text part `"[earlier picture no longer attached]"` (merged into the message's existing text part when there is one). Returns the number of images removed. Session calls it with `keep=2` at the end of every completed, non-handed-off turn. Cache note: a prune changes the replayed prefix once, costing one re-prefill (~2 s) on the following turn; it happens only when a third image is in history, so at most once per look after the second.

- [ ] **Step 1: Write the failing conversation test**

Append to `tests/test_conversation.py`:
```python
def test_prune_images_keeps_the_last_two_and_stubs_the_rest():
    from richard.conversation import Conversation, user_parts

    convo = Conversation()
    convo.add_user(user_parts("look", ["data:image/jpeg;base64,AAA"]))
    convo.add_assistant("A cup.")
    convo.add_user(user_parts(None, ["data:image/jpeg;base64,BBB"]))
    convo.add_assistant("A plant.")
    convo.add_user(user_parts("and now", ["data:image/jpeg;base64,CCC"]))
    convo.add_assistant("A book.")
    assert convo.prune_images(keep=2) == 1
    first = convo.history()[0].to_chat()["content"]
    assert first == [{"type": "text", "text": "look [earlier picture no longer attached]"}]
    later = [m.to_chat()["content"] for m in convo.history()[2:5:2]]
    assert all(any(p["type"] == "image_url" for p in c) for c in later)
    assert convo.prune_images(keep=2) == 0  # idempotent


def test_prune_images_stubs_text_free_image_messages():
    from richard.conversation import Conversation, user_parts

    convo = Conversation()
    for tag in ("AAA", "BBB", "CCC"):
        convo.add_user(user_parts(None, [f"data:image/jpeg;base64,{tag}"]))
        convo.add_assistant("seen")
    assert convo.prune_images(keep=2) == 1
    assert convo.history()[0].to_chat()["content"] == [
        {"type": "text", "text": "[earlier picture no longer attached]"}
    ]
```

- [ ] **Step 2: Run it to verify it fails**

Run: `.venv/bin/pytest tests/test_conversation.py -q -k prune`
Expected: FAIL with `AttributeError: 'Conversation' object has no attribute 'prune_images'`

- [ ] **Step 3: Implement prune_images**

In `src/richard/conversation.py`, add a module constant after `DEFAULT_SYSTEM_PROMPT`:
```python
PRUNED_IMAGE_STUB = "[earlier picture no longer attached]"
```
and add to `Conversation` (after `seal_pending`):
```python
    def prune_images(self, keep: int = 2) -> int:
        """Drop the image parts of all but the last `keep` image-carrying user messages.

        Every picture ever taken used to stay in the replayed history, so a long session
        carried an album the model had to attend to on every turn, with nothing marking
        which frame was current. A pruned message keeps its text and gains a short stub
        so the transcript still reads correctly. Changing an old message changes the
        cached prefix once; the caller decides when that is affordable.
        """
        indices = [
            i for i, m in enumerate(self._history)
            if m.role == "user" and isinstance(m.content, list)
            and any(isinstance(p, dict) and p.get("type") == "image_url" for p in m.content)
        ]
        removed = 0
        for i in indices[:-keep] if keep > 0 else indices:
            message = self._history[i]
            texts = [p["text"] for p in message.content if isinstance(p, dict) and p.get("type") == "text"]
            removed += sum(1 for p in message.content if isinstance(p, dict) and p.get("type") == "image_url")
            stub = " ".join([*texts, PRUNED_IMAGE_STUB]).strip()
            self._history[i] = Message(role="user", content=[{"type": "text", "text": stub}])
        return removed
```

- [ ] **Step 4: Run the conversation tests**

Run: `.venv/bin/pytest tests/test_conversation.py -q`
Expected: all pass

- [ ] **Step 5: Write the failing session test**

Append to `tests/test_browser_presence_backend.py`:
```python
def test_session_prunes_old_images_after_a_completed_turn():
    brain = ScriptBrain([{"deltas": ["One."]}, {"deltas": ["Two."]}, {"deltas": ["Three."]}])
    session, emitted = make_session(Engine(brain, [NoTools()], Personality()))
    try:
        for index in range(1, 4):
            session.create_item({"kind": "message", "content": [
                {"type": "text", "text": f"look {index}"},
                {"type": "image_url", "image_url": {"url": IMAGE}},
            ]})
            session.create_response()
            wait_until(lambda: len([e for e in emitted if e["type"] == "response.done"]) == index)
        history = [m.to_chat() for m in session.conversation.history()]
        image_messages = [
            m for m in history
            if isinstance(m["content"], list) and any(p.get("type") == "image_url" for p in m["content"])
        ]
        assert len(image_messages) == 2
        assert history[0]["content"] == [{"type": "text", "text": "look 1 [earlier picture no longer attached]"}]
    finally:
        session.close()
```

- [ ] **Step 6: Run it to verify it fails**

Run: `.venv/bin/pytest tests/test_browser_presence_backend.py::test_session_prunes_old_images_after_a_completed_turn -q`
Expected: FAIL, `assert 3 == 2`

- [ ] **Step 7: Call prune from the session**

In `src/richard/realtime/session.py`, `_respond`, replace:
```python
        if full and not handed_to_client and status == "completed":
            with self._state_lock:
                if token is self._active_token and not cancel.is_set():
                    self.conversation.add_assistant(full)
```
with:
```python
        if full and not handed_to_client and status == "completed":
            with self._state_lock:
                if token is self._active_token and not cancel.is_set():
                    self.conversation.add_assistant(full)
                    pruned = self.conversation.prune_images(keep=2)
                    if pruned:
                        log.info("pruned %d older image(s) from history", pruned)
```

- [ ] **Step 8: Run the session test and the suite**

Run: `.venv/bin/pytest tests/test_browser_presence_backend.py -q` then `.venv/bin/pytest -q`
Expected: all pass

- [ ] **Step 9: Commit**

```bash
git add src/richard/conversation.py src/richard/realtime/session.py tests/test_conversation.py tests/test_browser_presence_backend.py
git commit -m "conversation: keep only the last two images in replayed history"
```

---

### Task 7: The action nudge ignores hesitation phrases (proposal 7)

**Files:**
- Modify: `src/richard/engine.py` (`_PROMISE_RE` lines 32-38)
- Test: `tests/test_engine.py` (`test_promises_action_heuristic`)

- [ ] **Step 1: Extend the failing test**

Replace `test_promises_action_heuristic` in `tests/test_engine.py` with:
```python
def test_promises_action_heuristic():
    from richard.engine import _promises_action

    assert _promises_action("I'll turn the fan off.")
    assert _promises_action("Turning it off now.")
    assert _promises_action("Let me dim the lights.")
    assert not _promises_action("The fan is off.")
    assert not _promises_action("Done — lamp's off, confirmed at 40%.")
    # Hesitation and looking are not device actions; nudging them pushed the model
    # into camera calls (review 2026-09-16).
    assert not _promises_action("Mm, let me think.")
    assert not _promises_action("Let me see.")
    assert not _promises_action("Let me take a look.")
    assert not _promises_action("Let me work that out.")
    assert not _promises_action("One moment.")
    assert not _promises_action("Let me check the image.")
```

- [ ] **Step 2: Run it to verify it fails**

Run: `.venv/bin/pytest tests/test_engine.py::test_promises_action_heuristic -q`
Expected: FAIL at `assert not _promises_action("Mm, let me think.")`

- [ ] **Step 3: Narrow the regex**

In `src/richard/engine.py`, replace `_PROMISE_RE` with:
```python
# First-person commitments and present-progressive device verbs. "Let me" counts only
# when followed by something other than thinking or looking: "let me think", "let me
# see", "let me take a look" and "one moment" are hesitations, and nudging them sent the
# model looking for a tool to justify the phrase (review 2026-09-16). A false positive
# costs one short extra completion answered by the sentinel; a false negative is the
# status quo (the user repeats themselves), so the net is deliberately modest.
_PROMISE_RE = re.compile(
    r"(?i)\b(?:"
    r"i['’]?ll|i will|i['’]?m going to|i am going to|"
    r"let me(?!\s+(?:think|see|check|look|take a look|have a look|work))|"
    r"right away|"
    r"turning|switching|setting|dimming|starting|stopping|opening|closing|locking|unlocking"
    r")\b"
)
```

- [ ] **Step 4: Run the engine tests and the suite**

Run: `.venv/bin/pytest tests/test_engine.py -q` then `.venv/bin/pytest -q`
Expected: all pass

- [ ] **Step 5: Commit**

```bash
git add src/richard/engine.py tests/test_engine.py
git commit -m "engine: the action nudge ignores hesitation and looking phrases"
```

---

### Task 8: Deploy to CT123, fix the config, re-measure at 350 W (proposals 8 and 9)

**Files:**
- No repository files. CT123 config `/root/.richard/config.toml`, service `richard.service`.
- Docs: append the measured numbers to `reachy-lab/RESEARCH-FAST-DUPLEX-2026-09-16.md` section 2 and one dated line to `/root/CHANGELOG.md` on the CT.

Preconditions: Tasks 1-7 committed, `.venv/bin/pytest -q` green, the Node suite green (`npm test` in the worktree, 32 tests). Matteo has approved deployment ("do all", 2026-09-16).

- [ ] **Step 1: Push the branch**

```bash
cd /Users/matteo/Documents/GitHub/Richard/.worktrees/reachy-presence
.venv/bin/pytest -q && npm test --silent
git log --oneline b6a2fd4..HEAD   # expect the seven commits from Tasks 1-7
git push public reachy-presence
```
(`public` is the remote the deployed checkout pulls from; verify with `git remote -v` first. Run the public-push grep from memory `richard-public-push-check` before pushing.)

- [ ] **Step 2: Remove the reasoning_effort leftover on the CT**

```bash
~/Documents/Github/inference-box/bin/rbox 'cp /root/.richard/config.toml /root/work/misc/config.toml.bak-$(date +%Y%m%d)-reasoning-effort && sed -i "/^reasoning_effort = /d" /root/.richard/config.toml && grep -n -A2 "chat_template_kwargs" /root/.richard/config.toml'
```
Expected output shows only `enable_thinking = false` under the table.

- [ ] **Step 3: Update and restart Richard**

```bash
~/Documents/Github/inference-box/bin/rbox 'cd /opt/richard && git pull --ff-only && uv pip install --python .venv/bin/python -e . && systemctl restart richard && sleep 8 && systemctl is-active richard && git log --oneline -1'
```
Expected: `active` and the Task 7 commit hash. (The venv has no pip; `uv pip` is the documented path, memory `reachy-ct123-richard-box`.)

- [ ] **Step 4: Verify the service and the timing log**

```bash
~/Documents/Github/inference-box/bin/rbox 'ss -ltn | grep -E ":8766|:8771" | wc -l; nvidia-smi --query-gpu=power.limit,clocks.sm --format=csv,noheader'
```
Expected: `2`, and `350.00 W`. Then run the typed-turn smoke that exists on the CT (`/root/work/scripts/realtime_smoke.py`, used 2026-09-08) three times and collect the timing lines:
```bash
~/Documents/Github/inference-box/bin/rbox 'for i in 1 2 3; do python3 /root/work/scripts/realtime_smoke.py >/dev/null 2>&1; sleep 2; done; journalctl -u richard --since "-3 min" --no-pager | grep "turn timing" | tail -3'
```
Record the three `first_token` and `first_audio` values as the 350 W baseline (typed turn, so no `stt` mark). Ask Matteo for two spoken Italian turns in the browser and pull their lines the same way to get `stt`.

- [ ] **Step 5: Record the numbers and close the session**

Append to `reachy-lab/RESEARCH-FAST-DUPLEX-2026-09-16.md`, section 2, a dated sub-heading "Baseline at 350 W (2026-09-16, deployed <hash>)" with a three-column table (turn, first_token ms, first_audio ms) and the spoken-turn `stt` values. Then on the CT:
```bash
~/Documents/Github/inference-box/bin/rbox 'echo "- $(date -u +%F) Claude: deployed reachy-presence <hash> (vision pressure removal + turn timing); removed reasoning_effort from config (backup in work/misc); baseline at 350 W recorded in the lab research note." >> /root/CHANGELOG.md'
~/Documents/Github/inference-box/bin/pull-box-docs
cd ~/Documents/Github/inference-box && git add box/lxc123/CHANGELOG.md && git commit -m "box/lxc123: vision-pressure deploy + 350 W baseline (2026-09-16)"
```

- [ ] **Step 6: Tell Matteo what changed for him**

Reload the browser page and re-enable voice. The "fammi dare un'occhiata" cue now plays only on turns that carry a picture. Face tools appear only when faces are mentioned. Old pictures drop out of history after the second new one.

---

## Self-review

- Spec coverage: proposal 1 → Task 2; 2 → Task 2 (mechanism explained in its Interfaces); 3 → Task 3; 4 → Task 4; 5 → Task 5; 6 → Task 6; 7 → Task 7; 8 → Task 8 step 2; 9 → Task 1 (marks) + Task 8 step 4 (re-measure). Nothing left over.
- Placeholders: none; every code step carries the code. `<hash>` in Task 8 is filled at execution from `git log`.
- Type consistency: `wants_frame(user_text, *, unsolicited, context)` is used with the same keywords in Task 2's session change; `prune_images(keep=2) -> int` in Task 6 matches both tests; `all_schemas` / `conditional_schemas(user_text)` names match between provider and engine in Task 4; `TurnTiming.mark/line` match between timing tests and session wiring in Task 1.
