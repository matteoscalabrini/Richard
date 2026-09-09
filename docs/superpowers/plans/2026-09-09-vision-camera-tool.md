# Vision (spec 3a): multimodal messages, camera tool over realtime, web attach — implementation plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Richard sees: a picture attached in the web UI (typed or push-to-talk), and a frame the brain asks for through a `camera` tool over `/v1/realtime`, served by the browser's webcam today and by the stock Reachy Conversation App on the robot tomorrow, byte-for-byte the same protocol sequence.

**Architecture:** `Message.content` becomes a string or a list of chat-completions content parts that pass through the brain client unchanged. The engine learns client-side tools: a call it cannot execute is recorded in the conversation and yielded to the caller as `ClientToolCall`, ending the turn; the next turn runs on the appended history (tool result, then the image as a user message), so there is no suspended state. The realtime server adopts spec one's item semantics: `conversation.item.create` appends only (`message` with `input_text`/`input_image`, `function_call_output`), `response.create` runs the turn, `response.function_call_arguments.done` hands a call to the client. The web page attaches pictures on the typed and push-to-talk paths and, in voice mode, registers a `camera` tool and answers it from the webcam.

**Tech Stack:** Python 3.11+, numpy-only core (no image decoder: images are validated by header and size and passed through), httpx, websockets, pytest; vanilla JS in `static.py`.

## Global Constraints

- Spec: `docs/superpowers/specs/2026-09-09-vision-camera-tool-design.md`. Read it once before starting.
- Work in the worktree `~/Documents/GitHub/Richard/.worktrees/reachy-presence`, branch `reachy-presence`. Run tests with `.venv/bin/python -m pytest -q` from the worktree root (the default run excludes `gpu` and `slow`). Baseline at plan time: 595 passed, 2 skipped, 3 deselected.
- Commit messages end with `Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>`. Never push in this plan; pushing has its own check (memory `richard-public-push-check`).
- Content-part shapes are fixed: text `{"type": "text", "text": ...}`, image `{"type": "image_url", "image_url": {"url": "data:image/jpeg;base64,..."}}`. Data URLs accepted: `data:image/(jpeg|png|webp);base64,`. Decoded size cap: 8 MB.
- Prompts stay append-only; nothing in this plan rewrites an earlier message in a conversation. The system head stays static text (`persona.py`); no per-session prompt sections.
- The web UI is one HTML string in `src/richard/web/static.py` (`SPA_HTML`, a raw string). No build step, no external assets. Existing visual language: IBM Plex Mono, 2 px pixel borders, zero radius.
- Wire names must match the Reachy Conversation App: tool name `camera`, tool result `{"image_attached": true, "image_width": w, "image_height": h}`, user item content `[{"type": "input_image", "image_url": "data:image/jpeg;base64,..."}]`, error code `conversation_already_has_active_response`.

---

## File structure

| file | responsibility after this plan |
|---|---|
| `src/richard/conversation.py` | `Message` with string-or-parts content and `text()`; `user_parts()`; `Conversation` with `pending_client_calls()` / `seal_pending()` |
| `src/richard/vision.py` (new) | image data-URL validation, image counting in history, the two telemetry log lines |
| `src/richard/errors.py` | `BrainRejectedInput(BrainUnreachable)` |
| `src/richard/brain/llama_cpp.py` | 4xx on a request with images → `BrainRejectedInput`; parts pass through |
| `src/richard/engine.py` | `ClientToolCall`; `respond_streaming(conversation, client_tools=None)`; `tool_names()` |
| `src/richard/persona.py` | `PERCEPTION_RULES` appended to every head |
| `src/richard/realtime/events.py` | `response.create` accepted; `parse_item()`, `tools_to_schemas()`, `function_call_arguments_done()`, `ACTIVE_RESPONSE_CODE` |
| `src/richard/realtime/session.py` | `update()` with tools; `create_item()`; `create_response()`; client-call turn ending; sealing; image-rejected line |
| `src/richard/realtime/server.py` | dispatch of the new events; item parsing moved to `events.parse_item` |
| `src/richard/web/app.py` | parts on `/api/chat` and `/api/voice` with validation and 400s; rejected line |
| `src/richard/web/static.py` | attach button + chip + thumbnails; voice-mode `camera` tool from the webcam |
| `docs/realtime-api.md`, `docs/vision.md` | protocol and vision docs |

---

### Task 1: Message content parts and `text()`

**Files:**
- Modify: `src/richard/conversation.py`
- Test: `tests/test_conversation.py`

**Interfaces:**
- Produces: `Message.content: str | list[dict] | None`; `Message.text() -> str`; `user_parts(text: str | None, image_urls: list[str]) -> list[dict]`; `Conversation.add_user(content: str | list[dict])`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_conversation.py`:

```python
from richard.conversation import user_parts


IMG = "data:image/jpeg;base64,/9j/4AAQ"


def test_user_parts_builds_text_then_images():
    assert user_parts("what is this?", [IMG]) == [
        {"type": "text", "text": "what is this?"},
        {"type": "image_url", "image_url": {"url": IMG}},
    ]
    assert user_parts(None, [IMG]) == [{"type": "image_url", "image_url": {"url": IMG}}]
    assert user_parts("", [IMG]) == [{"type": "image_url", "image_url": {"url": IMG}}]


def test_parts_content_passes_through_to_chat_unchanged():
    convo = Conversation(system_prompt="sys")
    parts = user_parts("look", [IMG])
    convo.add_user(parts)
    convo.add_assistant("A mug.")
    assert [m.to_chat() for m in convo.history()] == [
        {"role": "user", "content": parts},
        {"role": "assistant", "content": "A mug."},
    ]


def test_message_text_joins_text_parts_only():
    assert Message("user", "plain").text() == "plain"
    assert Message("user", user_parts("look here", [IMG])).text() == "look here"
    assert Message("user", user_parts(None, [IMG])).text() == ""
    assert Message("assistant", None).text() == ""
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_conversation.py -q`
Expected: FAIL, `ImportError: cannot import name 'user_parts'`.

- [ ] **Step 3: Implement**

In `src/richard/conversation.py`, replace the `Message` dataclass and `add_user` with:

```python
@dataclass(frozen=True)
class Message:
    role: str
    # A string, or a list of OpenAI content parts ({"type": "text", ...} /
    # {"type": "image_url", ...}) served to the brain unchanged. Vision enters here.
    content: str | list[dict] | None
    # A tool round is stored exactly as it was served (assistant tool_calls, then one
    # tool message per call) so the next turn's prompt starts with the byte-identical
    # sequence the model already saw: the box's prompt cache only matches such a prefix.
    tool_calls: tuple[dict, ...] | None = None
    tool_call_id: str | None = None

    def to_chat(self) -> dict:
        message: dict = {"role": self.role, "content": self.content}
        if self.tool_calls is not None:
            message["tool_calls"] = [dict(call) for call in self.tool_calls]
        if self.tool_call_id is not None:
            message["tool_call_id"] = self.tool_call_id
        return message

    def text(self) -> str:
        """The message's text: the string content, or the joined text parts."""
        if isinstance(self.content, str):
            return self.content
        if isinstance(self.content, list):
            return " ".join(
                p["text"] for p in self.content
                if isinstance(p, dict) and p.get("type") == "text" and isinstance(p.get("text"), str)
            ).strip()
        return ""


def user_parts(text: str | None, image_urls: list[str]) -> list[dict]:
    """Content parts for a user message: the text part first (when given), then one
    image part per data URL. This is the only place the part shapes are spelled out."""
    parts: list[dict] = []
    if text:
        parts.append({"type": "text", "text": text})
    for url in image_urls:
        parts.append({"type": "image_url", "image_url": {"url": url}})
    return parts
```

and in `Conversation`:

```python
    def add_user(self, content: str | list[dict]) -> None:
        self._history.append(Message(role="user", content=content))
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_conversation.py -q`
Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add src/richard/conversation.py tests/test_conversation.py
git commit -m "conversation: content parts in Message, text() and user_parts()

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 2: Pending client calls and sealing

**Files:**
- Modify: `src/richard/conversation.py`
- Test: `tests/test_conversation.py`

**Interfaces:**
- Produces: `Conversation.pending_client_calls() -> list[str]`; `Conversation.seal_pending(reason: str) -> list[str]` (appends `{"error": reason}` tool results, returns the sealed ids).

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_conversation.py`:

```python
import json

CALLS = [{"id": "c1", "type": "function", "function": {"name": "camera", "arguments": '{"question": "what"}'}}]


def test_pending_client_calls_lists_unanswered_calls_of_last_round():
    convo = Conversation(system_prompt="sys")
    convo.add_user("look")
    convo.add_tool_call("Let me look.", CALLS)
    assert convo.pending_client_calls() == ["c1"]
    convo.add_tool_result("c1", '{"image_attached": true}')
    assert convo.pending_client_calls() == []


def test_pending_client_calls_is_empty_after_a_plain_reply_and_on_fresh_conversations():
    convo = Conversation(system_prompt="sys")
    assert convo.pending_client_calls() == []
    convo.add_user("hi")
    convo.add_tool_call(None, CALLS)
    convo.add_tool_result("c1", "ok")
    convo.add_user(user_parts(None, [IMG]))
    convo.add_assistant("A mug.")
    assert convo.pending_client_calls() == []


def test_pending_client_calls_handles_partially_answered_rounds():
    calls = [
        {"id": "a", "type": "function", "function": {"name": "remember", "arguments": "{}"}},
        {"id": "b", "type": "function", "function": {"name": "camera", "arguments": "{}"}},
    ]
    convo = Conversation(system_prompt="sys")
    convo.add_user("hi")
    convo.add_tool_call(None, calls)
    convo.add_tool_result("a", "Remembered.")
    assert convo.pending_client_calls() == ["b"]


def test_seal_pending_appends_error_results_and_returns_ids():
    convo = Conversation(system_prompt="sys")
    convo.add_user("look")
    convo.add_tool_call(None, CALLS)
    assert convo.seal_pending("no result from client") == ["c1"]
    last = convo.history()[-1]
    assert last.role == "tool" and last.tool_call_id == "c1"
    assert json.loads(last.content) == {"error": "no result from client"}
    assert convo.pending_client_calls() == []
    assert convo.seal_pending("again") == []
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_conversation.py -q`
Expected: FAIL, `AttributeError: 'Conversation' object has no attribute 'pending_client_calls'`.

- [ ] **Step 3: Implement**

Add `import json` at the top of `src/richard/conversation.py` and these methods to `Conversation` (after `add_tool_result`):

```python
    def pending_client_calls(self) -> list[str]:
        """Ids of the calls in the most recent tool round that have no tool result yet.

        Server-owned calls get their result appended by the engine in the same turn, so
        anything left pending was handed to the client (a `camera` on the robot or in
        the browser). A plain assistant reply closes the matter: nothing before it is pending.
        """
        answered: set[str] = set()
        for message in reversed(self._history):
            if message.role == "tool" and message.tool_call_id:
                answered.add(message.tool_call_id)
            elif message.role == "assistant" and message.tool_calls:
                return [c["id"] for c in message.tool_calls if c.get("id") not in answered]
            elif message.role == "assistant":
                return []
        return []

    def seal_pending(self, reason: str) -> list[str]:
        """Append an error result for every pending call so the served prefix is well formed
        (a tool call without a result confuses chat templates). Returns the sealed ids."""
        ids = self.pending_client_calls()
        for call_id in ids:
            self.add_tool_result(call_id, json.dumps({"error": reason}))
        return ids
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_conversation.py -q`
Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add src/richard/conversation.py tests/test_conversation.py
git commit -m "conversation: pending_client_calls() and seal_pending()

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 3: `richard.vision`: image validation and telemetry

**Files:**
- Create: `src/richard/vision.py`
- Test: `tests/test_vision.py`

**Interfaces:**
- Produces: `IMAGE_MAX_BYTES = 8 * 1024 * 1024`; `check_image_data_url(url) -> tuple[str, int]` (mime, decoded bytes; raises `ValueError`); `image_urls(content) -> list[str]`; `count_images(messages: list[Message]) -> int`; `log_image(source: str, mime: str, nbytes: int, history_images: int)`; `log_history(source: str, images: int)`; `log_client_call(name: str, arguments: str)`.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_vision.py`:

```python
import base64
import logging

import pytest

from richard.conversation import Message, user_parts
from richard.vision import (
    IMAGE_MAX_BYTES,
    check_image_data_url,
    count_images,
    image_urls,
    log_client_call,
    log_history,
    log_image,
)

JPEG = "data:image/jpeg;base64," + base64.b64encode(b"\xff\xd8\xff\xe0 fake jpeg").decode()


def test_check_accepts_jpeg_png_webp_and_reports_mime_and_size():
    assert check_image_data_url(JPEG) == ("image/jpeg", 14)
    png = "data:image/png;base64," + base64.b64encode(b"\x89PNG").decode()
    assert check_image_data_url(png) == ("image/png", 4)
    webp = "data:image/webp;base64," + base64.b64encode(b"RIFF").decode()
    assert check_image_data_url(webp) == ("image/webp", 4)


@pytest.mark.parametrize("url,fragment", [
    ("http://example.com/a.jpg", "data:image"),
    ("data:image/gif;base64,R0lG", "data:image"),
    ("data:image/jpeg;base64,@@@", "base64"),
    ("data:image/jpeg;base64,", "empty"),
    (None, "data URL"),
    (12, "data URL"),
])
def test_check_rejects_bad_urls(url, fragment):
    with pytest.raises(ValueError, match=fragment):
        check_image_data_url(url)


def test_check_rejects_oversized_payload():
    big = "data:image/jpeg;base64," + base64.b64encode(b"x" * (IMAGE_MAX_BYTES + 1)).decode()
    with pytest.raises(ValueError, match="8 MB"):
        check_image_data_url(big)


def test_image_urls_and_count_images():
    assert image_urls("plain") == []
    assert image_urls(user_parts("t", [JPEG, JPEG])) == [JPEG, JPEG]
    history = [Message("user", "hi"), Message("user", user_parts(None, [JPEG])), Message("assistant", "ok"),
               Message("user", user_parts("x", [JPEG]))]
    assert count_images(history) == 2


def test_log_lines_are_greppable(caplog):
    with caplog.at_level(logging.INFO, logger="richard.vision"):
        log_image("client", "image/jpeg", 1234, 2)
        log_history("web", 3)
        log_client_call("camera", '{"question": "what"}')
    assert "vision: image source=client mime=image/jpeg bytes=1234 history_images=2" in caplog.text
    assert "vision: history source=web images=3" in caplog.text
    assert 'vision: client tool call name=camera arguments={"question": "what"}' in caplog.text
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_vision.py -q`
Expected: FAIL, `ModuleNotFoundError: No module named 'richard.vision'`.

- [ ] **Step 3: Implement**

Create `src/richard/vision.py`:

```python
"""Vision helpers shared by the web and realtime edges.

The core has no image decoder (numpy only): an image is validated by its data-URL
header and decoded size, then passed to the brain untouched. Sizing happens where
the pixels are (the browser resizes to an 800 px long edge; the robot sends its native
frame). Telemetry is two INFO lines on the `richard.vision` logger so p50/p95 can be
computed from logs later, as for the spoken path.
"""
from __future__ import annotations

import base64
import binascii
import logging
import re

from richard.conversation import Message

log = logging.getLogger("richard.vision")

IMAGE_MAX_BYTES = 8 * 1024 * 1024
_DATA_URL = re.compile(r"^data:image/(jpeg|png|webp);base64,(.*)$", re.DOTALL)


def check_image_data_url(url) -> tuple[str, int]:
    """Validate an image data URL. Returns (mime, decoded size). Raises ValueError."""
    if not isinstance(url, str):
        raise ValueError("image must be a data URL string")
    match = _DATA_URL.match(url)
    if not match:
        raise ValueError("image must be a data:image/(jpeg|png|webp);base64 URL")
    try:
        raw = base64.b64decode(match.group(2), validate=True)
    except (binascii.Error, ValueError) as exc:
        raise ValueError("image payload is not valid base64") from exc
    if not raw:
        raise ValueError("image payload is empty")
    if len(raw) > IMAGE_MAX_BYTES:
        raise ValueError(f"image is larger than {IMAGE_MAX_BYTES // (1024 * 1024)} MB")
    return f"image/{match.group(1)}", len(raw)


def image_urls(content) -> list[str]:
    """The image data URLs in a message's content (none for string content)."""
    if not isinstance(content, list):
        return []
    urls = []
    for part in content:
        if isinstance(part, dict) and part.get("type") == "image_url":
            ref = part.get("image_url")
            if isinstance(ref, dict) and isinstance(ref.get("url"), str):
                urls.append(ref["url"])
    return urls


def count_images(messages: list[Message]) -> int:
    return sum(len(image_urls(m.content)) for m in messages)


def log_image(source: str, mime: str, nbytes: int, history_images: int) -> None:
    log.info("vision: image source=%s mime=%s bytes=%d history_images=%d",
             source, mime, nbytes, history_images)


def log_history(source: str, images: int) -> None:
    """The HTTP paths re-send the whole history each request and cannot tell a new image
    from an old one; they log the count instead."""
    log.info("vision: history source=%s images=%d", source, images)


def log_client_call(name: str, arguments: str) -> None:
    log.info("vision: client tool call name=%s arguments=%s", name, arguments)
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_vision.py -q`
Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add src/richard/vision.py tests/test_vision.py
git commit -m "vision: image data-URL validation, image counting, telemetry lines

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 4: Brain client: parts pass through, 4xx with images → `BrainRejectedInput`

**Files:**
- Modify: `src/richard/errors.py`
- Modify: `src/richard/brain/llama_cpp.py`
- Test: `tests/test_llama_cpp_client.py`

**Interfaces:**
- Produces: `richard.errors.BrainRejectedInput(BrainUnreachable)`; `LlamaCppBrain.complete/stream` raise it on a 4xx response to a request whose messages contain an image part (body logged, first 500 chars in the message).

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_llama_cpp_client.py`:

```python
from richard.brain.completion import StreamEvent  # noqa: E402 (grouped with the new tests)
from richard.conversation import user_parts  # noqa: E402
from richard.errors import BrainRejectedInput  # noqa: E402

_IMG = "data:image/jpeg;base64,/9j/4AAQ"


def test_complete_sends_content_parts_unchanged():
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured.update(json.loads(request.content))
        return httpx.Response(200, json={"choices": [{"message": {"content": "a mug"}}]})

    brain = LlamaCppBrain("http://box:8080", "m", client=_client(handler))
    parts = user_parts("what is this?", [_IMG])
    brain.complete([{"role": "system", "content": "s"}, {"role": "user", "content": parts}])
    assert captured["messages"][1] == {"role": "user", "content": parts}


def test_complete_4xx_with_images_raises_brain_rejected_input():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(400, text='{"error": "image input not supported"}')

    brain = LlamaCppBrain("http://box:8080", "m", client=_client(handler))
    with pytest.raises(BrainRejectedInput, match="400"):
        brain.complete([{"role": "user", "content": user_parts("look", [_IMG])}])


def test_complete_4xx_without_images_stays_brain_unreachable():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(400, text="bad")

    brain = LlamaCppBrain("http://box:8080", "m", client=_client(handler))
    with pytest.raises(BrainUnreachable) as excinfo:
        brain.complete([{"role": "user", "content": "hi"}])
    assert not isinstance(excinfo.value, BrainRejectedInput)


def test_stream_4xx_with_images_raises_brain_rejected_input():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(422, text="unprocessable")

    brain = LlamaCppBrain("http://box:8080", "m", client=_client(handler))
    with pytest.raises(BrainRejectedInput, match="422"):
        list(brain.stream([{"role": "user", "content": user_parts(None, [_IMG])}]))


def test_stream_passes_parts_and_yields_deltas():
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured.update(json.loads(request.content))
        body = 'data: {"choices": [{"delta": {"content": "a mug"}}]}\n\ndata: [DONE]\n\n'
        return httpx.Response(200, text=body)

    brain = LlamaCppBrain("http://box:8080", "m", client=_client(handler))
    parts = user_parts("look", [_IMG])
    events = list(brain.stream([{"role": "user", "content": parts}]))
    assert captured["messages"] == [{"role": "user", "content": parts}]
    assert events[0].delta == "a mug" and events[-1].done
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_llama_cpp_client.py -q`
Expected: FAIL, `ImportError: cannot import name 'BrainRejectedInput'`.

- [ ] **Step 3: Implement**

Append to `src/richard/errors.py`:

```python
class BrainRejectedInput(BrainUnreachable):
    """The brain answered 4xx to a request carrying images: the model or server cannot
    take that input. Callers speak a specific line and do not retry."""
```

In `src/richard/brain/llama_cpp.py` add the import and two helpers after `_parse_arguments`:

```python
from richard.errors import BrainRejectedInput, BrainUnreachable


def _has_images(messages: list[dict]) -> bool:
    for message in messages:
        content = message.get("content")
        if isinstance(content, list) and any(
            isinstance(p, dict) and p.get("type") == "image_url" for p in content
        ):
            return True
    return False


def _check_status(response: httpx.Response, messages: list[dict]) -> None:
    """raise_for_status, except that a 4xx on a request with images is a rejected
    input (the model cannot see), not an unreachable brain."""
    if 400 <= response.status_code < 500 and _has_images(messages):
        try:
            body = response.read()[:500].decode(errors="replace")
        except Exception:  # body unreadable; the status is the point
            body = ""
        raise BrainRejectedInput(f"brain rejected the request ({response.status_code}): {body}")
    response.raise_for_status()
```

Then in `complete`, replace `response.raise_for_status()` with `_check_status(response, messages)`, and in `stream` replace the `response.raise_for_status()` inside the `with` block with `_check_status(response, messages)`. `BrainRejectedInput` is not an `httpx.HTTPError`, so it passes the existing `except httpx.HTTPError` untouched.

- [ ] **Step 4: Run the tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_llama_cpp_client.py tests/test_brain_stream.py -q`
Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add src/richard/errors.py src/richard/brain/llama_cpp.py tests/test_llama_cpp_client.py
git commit -m "brain: content parts pass through; 4xx with images raises BrainRejectedInput

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 5: Engine client-side tools

**Files:**
- Modify: `src/richard/engine.py`
- Test: `tests/test_engine_streaming.py`

**Interfaces:**
- Produces: `richard.engine.ClientToolCall(id: str, name: str, arguments: str)` (frozen dataclass; `arguments` is the JSON string); `Engine.respond_streaming(conversation, client_tools: list[dict] | None = None) -> Iterator[str | ClientToolCall]`; `Engine.tool_names() -> list[str]` (provider tool names).
- Consumes: `Conversation.add_tool_call/add_tool_result` (existing).

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_engine_streaming.py`:

```python
import json

from richard.engine import ClientToolCall

CAMERA = {"type": "function", "function": {"name": "camera", "parameters": {
    "type": "object", "properties": {"question": {"type": "string"}}, "required": ["question"]}}}


def test_client_tool_call_ends_the_turn_and_is_yielded():
    brain = FakeBrain([
        {"deltas": ["Let me look. "], "tool_calls": [ToolCall(id="c1", name="camera", arguments={"question": "what"})]},
    ])
    provider = FakeProvider()
    engine = Engine(brain, [provider], Personality())
    convo = Conversation()
    convo.add_user("what am I holding?")
    out = list(engine.respond_streaming(convo, client_tools=[CAMERA]))
    assert out[0] == "Let me look. "
    assert out[1] == ClientToolCall(id="c1", name="camera", arguments=json.dumps({"question": "what"}))
    assert len(out) == 2
    assert provider.executed == []
    # the brain saw both schemas: the provider's and the client's
    assert [t["function"]["name"] for t in brain.tools[0]] == ["set_fan", "camera"]
    # history: user, assistant tool call with the spoken text; nothing else (no tool result yet)
    hist = [m.to_chat() for m in convo.history()]
    assert hist[1]["role"] == "assistant" and hist[1]["content"] == "Let me look. "
    assert hist[1]["tool_calls"][0]["id"] == "c1"
    assert len(hist) == 2
    assert convo.pending_client_calls() == ["c1"]


def test_next_turn_runs_on_the_appended_history():
    brain = FakeBrain([
        {"tool_calls": [ToolCall(id="c1", name="camera", arguments={"question": "what"})]},
        {"deltas": ["A blue mug."]},
    ])
    engine = Engine(brain, [FakeProvider()], Personality())
    convo = Conversation()
    convo.add_user("look")
    list(engine.respond_streaming(convo, client_tools=[CAMERA]))
    convo.add_tool_result("c1", '{"image_attached": true}')
    convo.add_user([{"type": "image_url", "image_url": {"url": "data:image/jpeg;base64,AAAA"}}])
    assert "".join(engine.respond_streaming(convo, client_tools=[CAMERA])) == "A blue mug."
    served = brain.calls[1]
    assert [m["role"] for m in served] == ["system", "user", "assistant", "tool", "user"]
    assert served[3]["tool_call_id"] == "c1"
    assert served[4]["content"][0]["type"] == "image_url"


def test_mixed_round_executes_provider_calls_and_defers_client_calls():
    brain = FakeBrain([
        {"tool_calls": [ToolCall(id="a", name="set_fan", arguments={"on": True}),
                        ToolCall(id="b", name="camera", arguments={"question": "q"})]},
    ])
    provider = FakeProvider()
    engine = Engine(brain, [provider], Personality())
    convo = Conversation()
    convo.add_user("fan on and look")
    out = list(engine.respond_streaming(convo, client_tools=[CAMERA]))
    assert provider.executed == [("set_fan", {"on": True})]
    assert out == [ClientToolCall(id="b", name="camera", arguments=json.dumps({"question": "q"}))]
    assert convo.pending_client_calls() == ["b"]
    roles = [m.role for m in convo.history()]
    assert roles == ["user", "assistant", "tool"]  # set_fan's result recorded, camera's pending


def test_client_tool_colliding_with_a_provider_is_dropped():
    brain = FakeBrain([{"deltas": ["ok"]}])
    engine = Engine(brain, [FakeProvider()], Personality())
    convo = Conversation()
    convo.add_user("hi")
    clash = {"type": "function", "function": {"name": "set_fan", "parameters": {}}}
    list(engine.respond_streaming(convo, client_tools=[clash, CAMERA]))
    assert [t["function"]["name"] for t in brain.tools[0]] == ["set_fan", "camera"]
    assert engine.tool_names() == ["set_fan"]


def test_client_call_counts_as_an_action_so_no_nudge_follows():
    brain = FakeBrain([
        {"deltas": ["I'll take a look."], "tool_calls": [ToolCall(id="c1", name="camera", arguments={"question": "q"})]},
    ])
    engine = Engine(brain, [FakeProvider()], Personality())
    convo = Conversation()
    convo.add_user("look")
    out = list(engine.respond_streaming(convo, client_tools=[CAMERA]))
    assert isinstance(out[-1], ClientToolCall)
    assert len(brain.calls) == 1  # no ACTION CHECK round


def test_without_client_tools_only_strings_are_yielded():
    brain = FakeBrain([{"deltas": ["plain"]}])
    engine = Engine(brain, [FakeProvider()], Personality())
    convo = Conversation()
    convo.add_user("hi")
    assert all(isinstance(x, str) for x in engine.respond_streaming(convo))
```

Also change the existing `FakeBrain.stream` in this test file to record the tools it was given:

```python
    def stream(self, messages, tools=None):
        self.calls.append(list(messages))
        self.tools.append(list(tools or []))
        script = self.scripts.pop(0)
        ...
```

and add `self.tools = []` in its `__init__`.

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_engine_streaming.py -q`
Expected: FAIL, `ImportError: cannot import name 'ClientToolCall'`.

- [ ] **Step 3: Implement**

In `src/richard/engine.py`, add after the imports:

```python
from dataclasses import dataclass


@dataclass(frozen=True)
class ClientToolCall:
    """A tool call the engine cannot execute: it belongs to the connected client (the
    Reachy app's `camera`, the browser's webcam). The engine records the call in the
    conversation and ends the turn; the client posts the result as a tool message (and,
    for a camera, the image as a user message) and asks for a new response."""

    id: str
    name: str
    arguments: str  # JSON, as served to the client
```

Add to `Engine`:

```python
    def tool_names(self) -> list[str]:
        """Names of the tools Richard's providers own (client tools with these names are dropped)."""
        return [s["function"]["name"] for provider in self._providers for s in provider.schemas()]

    def _client_schemas(self, client_tools: list[dict] | None) -> list[dict]:
        owned = set(self.tool_names())
        return [t for t in (client_tools or []) if t["function"]["name"] not in owned]
```

Change `_record_tool_round` to skip execution for deferred calls:

```python
    def _record_tool_round(
        self, conversation: Conversation, working: list[dict], completion: Completion,
        deferred: frozenset[str] = frozenset(),
    ) -> None:
        """Execute the calls and append the round to both the request and the history.

        The round is persisted verbatim (see Message): the next turn must replay the
        exact served prefix or the prompt cache misses and the whole prompt is
        re-prefilled (measured 2026-09-09: ~2 s per turn after every tool call). The
        nudge round is deliberately not persisted; it is rare and a fake user message
        in history would mislead later turns. Calls in `deferred` belong to the client:
        their result arrives later as a tool message posted by the client.
        """
        tool_message = _assistant_tool_call_message(completion)
        working.append(tool_message)
        conversation.add_tool_call(tool_message["content"], tool_message["tool_calls"])
        for call in completion.tool_calls:
            if call.id in deferred:
                continue
            result = self._execute(call.name, call.arguments)
            working.append({"role": "tool", "tool_call_id": call.id, "content": result})
            conversation.add_tool_result(call.id, result)
```

Replace `respond_streaming` with:

```python
    def respond_streaming(
        self, conversation: Conversation, client_tools: list[dict] | None = None
    ) -> Iterator[str | ClientToolCall]:
        working: list[dict] = [{"role": "system", "content": self._head(conversation)}]
        working += [m.to_chat() for m in conversation.history()]
        client_schemas = self._client_schemas(client_tools)
        client_names = {s["function"]["name"] for s in client_schemas}
        schemas = [s for provider in self._providers for s in provider.schemas()] + client_schemas
        any_tool_call = False
        nudged = False
        hold = False  # buffer the nudge round so a sentinel reply is never spoken
        turn_text = ""
        for _ in range(self._max_rounds):
            spoken = ""
            tool_calls = []
            for event in self._brain.stream(working, schemas):
                if event.delta:
                    spoken += event.delta
                    if not hold:
                        yield event.delta
                if event.done:
                    tool_calls = event.tool_calls
            turn_text += spoken
            if hold:
                hold = False
                if _is_nothing_to_run(spoken):
                    if not tool_calls:
                        return  # false-positive nudge; the sentinel stays silent
                    spoken = ""
                elif spoken:
                    yield spoken
            if not tool_calls:
                if (
                    not any_tool_call
                    and not nudged
                    and schemas
                    and _promises_action(turn_text)
                ):
                    nudged = True
                    hold = True
                    working.append({"role": "assistant", "content": spoken})
                    working.append({"role": "user", "content": NUDGE_PROMPT})
                    continue
                return
            any_tool_call = True
            client_calls = [c for c in tool_calls if c.name in client_names]
            self._record_tool_round(
                conversation, working, Completion(content=spoken or None, tool_calls=tool_calls),
                deferred=frozenset(c.id for c in client_calls),
            )
            if client_calls:
                # The turn ends here: the client owns the next step. The next
                # response.create runs a fresh turn on the appended history.
                for call in client_calls:
                    yield ClientToolCall(id=call.id, name=call.name, arguments=json.dumps(call.arguments))
                return
        # max_rounds exhausted: the generator just stops; the caller (voice loop) is
        # responsible for any fallback. (respond() returns an error string here instead.)
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_engine_streaming.py tests/test_engine.py -q`
Expected: all pass (the existing streaming tests still pass: with no client tools nothing changes).

- [ ] **Step 5: Commit**

```bash
git add src/richard/engine.py tests/test_engine_streaming.py
git commit -m "engine: client-side tools; a deferred call ends the turn as ClientToolCall

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 6: Perception rules in the system head

**Files:**
- Modify: `src/richard/persona.py`
- Test: `tests/test_persona.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/test_persona.py`:

```python
def test_prompt_ends_with_static_perception_rules():
    from richard.persona import ACTION_RULES, PERCEPTION_RULES

    prompt = build_system_prompt(Personality())
    assert prompt.endswith(f"{ACTION_RULES}\n\n{PERCEPTION_RULES}")
    assert "cannot see right now" in PERCEPTION_RULES
    assert "never describe a scene you have not been shown" in PERCEPTION_RULES
    # custom prompts get the same contract
    custom = build_system_prompt(Personality(system_prompt="You are {name}."))
    assert custom.endswith(PERCEPTION_RULES)
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_persona.py -q`
Expected: FAIL, `ImportError: cannot import name 'PERCEPTION_RULES'`.

- [ ] **Step 3: Implement**

In `src/richard/persona.py`, after `ACTION_RULES`:

```python
# Static on purpose: the head is pinned per conversation for prefix caching, so what
# Richard can see is phrased conditionally instead of varying with the session. The
# camera tool's own description says when to look.
PERCEPTION_RULES = (
    "Perception: you see only through pictures — an image attached to a message, or a "
    "frame you take by calling a camera tool when one is offered. With neither, say you "
    "cannot see right now; never describe a scene you have not been shown. A picture "
    "shows one moment from one viewpoint."
)
```

and change the return of `build_system_prompt` to:

```python
    return f"{base}\n\n{settings}\n\n{ACTION_RULES}\n\n{PERCEPTION_RULES}"
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_persona.py tests/test_engine.py -q`
Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add src/richard/persona.py tests/test_persona.py
git commit -m "persona: static perception rules in every system head

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 7: Realtime events: `response.create`, items, tools, function-call builder

**Files:**
- Modify: `src/richard/realtime/events.py`
- Test: `tests/test_realtime_events.py`

**Interfaces:**
- Produces: `"response.create"` in `CLIENT_EVENT_TYPES`; `parse_item(item: dict) -> dict` returning `{"kind": "message", "content": str | list[dict]}` or `{"kind": "function_call_output", "call_id": str, "output": str}` (raises `ValueError`); `tools_to_schemas(tools, reserved=()) -> tuple[list[dict], list[str]]`; `function_call_arguments_done(response_id, call_id, name, arguments) -> dict`; `ACTIVE_RESPONSE_CODE = "conversation_already_has_active_response"`.
- Consumes: `richard.vision.check_image_data_url`, `richard.conversation.user_parts`.

- [ ] **Step 1: Write the failing tests**

Replace the `test_parse_rejects_bad_events` parametrization entry `('{"type": "response.create"}', "unknown")` with `('{"type": "response.nope"}', "unknown")`, then append to `tests/test_realtime_events.py`:

```python
IMG = "data:image/jpeg;base64,/9j/4AAQ"


def test_response_create_is_a_known_client_event():
    assert events.parse_client_event('{"type": "response.create"}')["type"] == "response.create"


def test_parse_item_text_only_message_is_a_string():
    item = {"type": "message", "role": "user", "content": [{"type": "input_text", "text": " hi "}]}
    assert events.parse_item(item) == {"kind": "message", "content": "hi"}


def test_parse_item_without_type_is_a_message():
    assert events.parse_item({"content": [{"type": "input_text", "text": "hi"}]}) == {"kind": "message", "content": "hi"}


def test_parse_item_image_message_becomes_parts():
    item = {"type": "message", "role": "user", "content": [
        {"type": "input_text", "text": "what"}, {"type": "input_image", "image_url": IMG}]}
    assert events.parse_item(item) == {"kind": "message", "content": [
        {"type": "text", "text": "what"}, {"type": "image_url", "image_url": {"url": IMG}}]}
    only_image = {"type": "message", "role": "user", "content": [{"type": "input_image", "image_url": IMG}]}
    assert events.parse_item(only_image)["content"] == [{"type": "image_url", "image_url": {"url": IMG}}]


def test_parse_item_function_call_output():
    item = {"type": "function_call_output", "call_id": "c1", "output": '{"image_attached": true}'}
    assert events.parse_item(item) == {"kind": "function_call_output", "call_id": "c1", "output": '{"image_attached": true}'}


@pytest.mark.parametrize("item,fragment", [
    ({"type": "message", "content": "not a list"}, "content"),
    ({"type": "message", "content": []}, "no usable content"),
    ({"type": "message", "content": [{"type": "input_image", "image_url": "http://x/y.jpg"}]}, "data:image"),
    ({"type": "message", "role": "assistant", "content": [{"type": "input_text", "text": "x"}]}, "role"),
    ({"type": "function_call_output", "output": "x"}, "call_id"),
    ({"type": "function_call_output", "call_id": "c1", "output": 5}, "output"),
    ({"type": "function_call"}, "unsupported"),
])
def test_parse_item_rejects(item, fragment):
    with pytest.raises(ValueError, match=fragment):
        events.parse_item(item)


def test_tools_to_schemas_converts_flat_specs_and_drops_reserved():
    tools = [
        {"type": "function", "name": "camera", "description": "look", "parameters": {"type": "object", "properties": {}}},
        {"type": "function", "name": "remember", "description": "x", "parameters": {}},
        {"type": "function", "name": "", "parameters": {}},
        "junk",
    ]
    schemas, dropped = events.tools_to_schemas(tools, reserved={"remember"})
    assert schemas == [{"type": "function", "function": {
        "name": "camera", "description": "look", "parameters": {"type": "object", "properties": {}}}}]
    assert dropped == ["remember"]


def test_tools_to_schemas_defaults_missing_parameters():
    schemas, _ = events.tools_to_schemas([{"name": "go_to_sleep"}])
    assert schemas[0]["function"]["parameters"] == {"type": "object", "properties": {}}
    assert schemas[0]["function"]["description"] == ""


def test_function_call_arguments_done_shape():
    e = events.function_call_arguments_done("resp_1", "call_1", "camera", '{"question": "q"}')
    assert e["type"] == "response.function_call_arguments.done"
    assert e["response_id"] == "resp_1" and e["call_id"] == "call_1"
    assert e["name"] == "camera" and e["arguments"] == '{"question": "q"}'
    assert e["item_id"].startswith("item_") and e["event_id"].startswith("event_")
    assert e["output_index"] == 0


def test_active_response_code():
    assert events.ACTIVE_RESPONSE_CODE == "conversation_already_has_active_response"
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_realtime_events.py -q`
Expected: FAIL (`parse_client_event` rejects `response.create`; `parse_item` missing).

- [ ] **Step 3: Implement**

In `src/richard/realtime/events.py`:

```python
from richard.conversation import user_parts
from richard.vision import check_image_data_url

CLIENT_EVENT_TYPES = {
    "session.update",
    "input_audio_buffer.append",
    "response.cancel",
    "response.create",
    "conversation.item.create",
}

ACTIVE_RESPONSE_CODE = "conversation_already_has_active_response"
```

Add the builders and parsers (after `error`):

```python
def function_call_arguments_done(response_id: str, call_id: str, name: str, arguments: str) -> dict:
    """The brain called a client-owned tool; the client runs it and posts the result."""
    return {
        "type": "response.function_call_arguments.done",
        "event_id": new_id("event"),
        "response_id": response_id,
        "item_id": new_id("item"),
        "output_index": 0,
        "call_id": call_id,
        "name": name,
        "arguments": arguments,
    }


def tools_to_schemas(tools, reserved=()) -> tuple[list[dict], list[str]]:
    """Realtime flat function specs → chat-completions schemas. Names in `reserved`
    (Richard's own providers) are dropped and reported; junk entries are skipped."""
    schemas: list[dict] = []
    dropped: list[str] = []
    for tool in tools or []:
        if not isinstance(tool, dict) or tool.get("type", "function") != "function":
            continue
        name = tool.get("name")
        if not isinstance(name, str) or not name:
            continue
        if name in reserved:
            dropped.append(name)
            continue
        parameters = tool.get("parameters")
        if not isinstance(parameters, dict) or not parameters:
            parameters = {"type": "object", "properties": {}}
        schemas.append({"type": "function", "function": {
            "name": name,
            "description": str(tool.get("description") or ""),
            "parameters": parameters,
        }})
    return schemas, dropped


def parse_item(item: dict) -> dict:
    """Normalize a conversation.item.create item. Raises ValueError with a client-facing message.

    `message` (default): user role only; `input_text` and `input_image` parts; a text-only
    item becomes a string, anything with an image becomes content parts.
    `function_call_output`: `call_id` and a string `output`.
    """
    itype = item.get("type", "message")
    if itype == "message":
        if item.get("role", "user") != "user":
            raise ValueError("conversation.item.create: only user-role messages are accepted")
        parts = item.get("content")
        if not isinstance(parts, list):
            raise ValueError("conversation.item.create: 'content' must be a list of parts")
        texts: list[str] = []
        images: list[str] = []
        for part in parts:
            if not isinstance(part, dict):
                continue
            if part.get("type") == "input_text" and isinstance(part.get("text"), str):
                texts.append(part["text"].strip())
            elif part.get("type") == "input_image":
                check_image_data_url(part.get("image_url"))
                images.append(part["image_url"])
        text = " ".join(t for t in texts if t).strip()
        if not text and not images:
            raise ValueError("conversation.item.create: no usable content")
        return {"kind": "message", "content": text if not images else user_parts(text or None, images)}
    if itype == "function_call_output":
        call_id, output = item.get("call_id"), item.get("output")
        if not isinstance(call_id, str) or not call_id:
            raise ValueError("function_call_output: 'call_id' must be a non-empty string")
        if not isinstance(output, str):
            raise ValueError("function_call_output: 'output' must be a string")
        return {"kind": "function_call_output", "call_id": call_id, "output": output}
    raise ValueError(f"conversation.item.create: unsupported item type: {itype}")
```

Update the module docstring's first lines to mention the item kinds (one sentence).

- [ ] **Step 4: Run the tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_realtime_events.py -q`
Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add src/richard/realtime/events.py tests/test_realtime_events.py
git commit -m "realtime events: response.create, parse_item, tools_to_schemas, function_call_arguments.done

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 8: Session: tools, items, `response.create`, client-call turns

**Files:**
- Modify: `src/richard/realtime/session.py`
- Test: `tests/test_realtime_session.py`

**Interfaces:**
- Produces: `RealtimeSession.update(patch)` returns `{"barge_in", "tools": [names], "dropped_tools": [names]}` and accepts `patch["tools"]` (flat specs); `RealtimeSession.create_item(item: dict)` (a `parse_item` result; raises `ValueError` for an unknown or answered `call_id`); `RealtimeSession.create_response()`; `RealtimeSession.client_tools: list[dict]`. `create_text_item` is removed.
- Consumes: `Engine.respond_streaming(conversation, client_tools=...)`, `Engine.tool_names()` (optional via `getattr`), `ClientToolCall`, `Conversation.seal_pending`, `events.function_call_arguments_done`, `events.ACTIVE_RESPONSE_CODE`, `vision.log_image/log_client_call/count_images/image_urls/check_image_data_url`.

- [ ] **Step 1: Write the failing tests**

In `tests/test_realtime_session.py`, replace `test_text_item_runs_a_turn_without_stt` with:

```python
def test_text_item_appends_and_response_create_runs_the_turn():
    session, emitted, done = collect_session(detector=ScriptedDetector([]))
    session.create_item({"kind": "message", "content": "hello richard"})
    time.sleep(0.2)
    assert not any(e["type"] == "response.created" for e in emitted)  # append only
    session.create_response()
    wait(done)
    kinds = [e["type"] for e in emitted]
    assert "conversation.item.input_audio_transcription.completed" not in kinds
    assert "response.audio.delta" in kinds
    assert [m.role for m in session.conversation.history()] == ["user", "assistant"]
    session.close()
```

Then append:

```python
from richard.engine import ClientToolCall
from richard.errors import BrainRejectedInput

IMG = "data:image/jpeg;base64,/9j/4AAQ"
CAMERA_SPEC = {"type": "function", "name": "camera", "description": "look",
               "parameters": {"type": "object", "properties": {"question": {"type": "string"}}}}


class CameraEngine:
    """First turn: speaks then calls camera. Second turn: answers with the image in view."""

    def __init__(self):
        self.seen = []
        self.client_tools = []
        self.turns = 0

    def tool_names(self):
        return ["remember", "forget"]

    def respond_streaming(self, conversation, client_tools=None):
        self.client_tools.append(list(client_tools or []))
        self.seen.append([m.to_chat() for m in conversation.history()])
        self.turns += 1
        if self.turns == 1:
            yield "Let me look. "
            conversation.add_tool_call("Let me look. ", [{"id": "c1", "type": "function", "function": {
                "name": "camera", "arguments": '{"question": "what"}'}}])
            yield ClientToolCall(id="c1", name="camera", arguments='{"question": "what"}')
            return
        yield "A blue mug."


def test_session_update_stores_tools_and_reports_drops():
    session, emitted, done = collect_session(engine=CameraEngine(), detector=ScriptedDetector([]))
    out = session.update({"tools": [CAMERA_SPEC, {"type": "function", "name": "remember", "parameters": {}}],
                          "type": "realtime", "instructions": "ignored"})
    assert out["tools"] == ["camera"] and out["dropped_tools"] == ["remember"]
    assert out["barge_in"] == "vad"
    assert session.client_tools[0]["function"]["name"] == "camera"
    session.close()


def test_camera_round_trip_follows_the_app_sequence():
    engine = CameraEngine()
    session, emitted, done = collect_session(engine=engine, detector=ScriptedDetector([]))
    session.update({"tools": [CAMERA_SPEC]})
    session.create_item({"kind": "message", "content": "what am I holding?"})
    session.create_response()
    wait(done)
    kinds = [e["type"] for e in emitted]
    fc = kinds.index("response.function_call_arguments.done")
    assert kinds.index("response.created") < kinds.index("response.output_text.delta") < fc < kinds.index("response.done")
    call = emitted[fc]
    assert call["call_id"] == "c1" and call["name"] == "camera" and call["arguments"] == '{"question": "what"}'
    assert emitted[-1]["response"]["status"] == "completed"
    assert engine.client_tools[0][0]["function"]["name"] == "camera"
    # the turn that ended in a client call stores no assistant reply of its own
    assert [m.role for m in session.conversation.history()] == ["user", "assistant"]
    assert session.conversation.pending_client_calls() == ["c1"]

    done.clear()
    session.create_item({"kind": "function_call_output", "call_id": "c1", "output": '{"image_attached": true}'})
    session.create_item({"kind": "message", "content": [{"type": "image_url", "image_url": {"url": IMG}}]})
    session.create_response()
    wait(done)
    served = engine.seen[1]
    assert [m["role"] for m in served] == ["user", "assistant", "tool", "user"]
    assert served[2] == {"role": "tool", "tool_call_id": "c1", "content": '{"image_attached": true}'}
    assert served[3]["content"] == [{"type": "image_url", "image_url": {"url": IMG}}]
    assert [m.role for m in session.conversation.history()] == ["user", "assistant", "tool", "user", "assistant"]
    assert session.conversation.history()[-1].content == "A blue mug."
    session.close()


def test_function_call_output_for_unknown_call_is_rejected():
    session, emitted, done = collect_session(engine=CameraEngine(), detector=ScriptedDetector([]))
    with pytest.raises(ValueError, match="call_id"):
        session.create_item({"kind": "function_call_output", "call_id": "nope", "output": "{}"})
    assert session.conversation.history() == []
    session.close()


def test_response_create_with_nothing_new_is_an_empty_response():
    session, emitted, done = collect_session(detector=ScriptedDetector([]))
    session.create_response()  # empty conversation
    wait(done)
    kinds = [e["type"] for e in emitted]
    assert kinds[-2:] == ["response.created", "response.done"]
    assert "response.audio.delta" not in kinds
    session.close()


def test_response_create_while_active_is_an_error():
    tts = GatedTTS()
    session, emitted, done = collect_session(tts=tts, detector=ScriptedDetector([]))
    session.create_item({"kind": "message", "content": "hi"})
    session.create_response()
    assert tts.entered.wait(5)
    session.create_response()  # second one while speaking
    errors = [e for e in emitted if e["type"] == "error"]
    assert errors and errors[0]["error"]["code"] == "conversation_already_has_active_response"
    tts.release.set()
    wait(done)
    session.close()


def test_speech_over_a_pending_call_seals_it_first():
    engine = CameraEngine()
    session, emitted, done = collect_session(
        engine=engine, detector=ScriptedDetector([[("speech_started",)], [("utterance", b"pcm")]]))
    session.update({"tools": [CAMERA_SPEC]})
    session.create_item({"kind": "message", "content": "look"})
    session.create_response()
    wait(done)
    done.clear()
    session.feed_audio(FRAME)  # user talks instead of the client answering the call
    session.feed_audio(FRAME)
    wait(done)
    served = engine.seen[1]
    assert [m["role"] for m in served] == ["user", "assistant", "tool", "user"]
    assert json.loads(served[2]["content"]) == {"error": "no result from client"}
    session.close()


def test_response_create_seals_dangling_calls_before_running():
    engine = CameraEngine()
    session, emitted, done = collect_session(engine=engine, detector=ScriptedDetector([]))
    session.update({"tools": [CAMERA_SPEC]})
    session.create_item({"kind": "message", "content": "look"})
    session.create_response()
    wait(done)
    done.clear()
    session.create_response()  # client never posted the output
    wait(done)
    served = engine.seen[1]
    assert [m["role"] for m in served] == ["user", "assistant", "tool"]
    assert json.loads(served[2]["content"]) == {"error": "no result from client"}
    session.close()


class RejectingEngine:
    def respond_streaming(self, conversation, client_tools=None):
        raise BrainRejectedInput("brain rejected the request (400): no vision")
        yield  # pragma: no cover


def test_rejected_image_speaks_its_own_line():
    tts = FakeTTS()
    session, emitted, done = collect_session(engine=RejectingEngine(), tts=tts, detector=ScriptedDetector([]))
    session.create_item({"kind": "message", "content": [{"type": "image_url", "image_url": {"url": IMG}}]})
    session.create_response()
    wait(done)
    assert tts.spoken == ["I couldn't take that picture in."]
    assert emitted[-1]["response"]["status"] == "failed"
    codes = [e["error"]["code"] for e in emitted if e["type"] == "error"]
    assert codes == ["brain_rejected_input"]
    session.close()


def test_image_item_logs_telemetry(caplog):
    import logging
    session, emitted, done = collect_session(detector=ScriptedDetector([]))
    with caplog.at_level(logging.INFO, logger="richard.vision"):
        session.create_item({"kind": "message", "content": [{"type": "image_url", "image_url": {"url": IMG}}]})
    assert "vision: image source=client mime=image/jpeg" in caplog.text
    session.close()
```

Add `import json` and `import pytest` at the top of the test module.

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_realtime_session.py -q`
Expected: FAIL, `AttributeError: 'RealtimeSession' object has no attribute 'create_item'`.

- [ ] **Step 3: Implement**

Rewrite these parts of `src/richard/realtime/session.py`.

Imports and lines:

```python
from richard.conversation import Conversation
from richard.engine import ClientToolCall
from richard.errors import BrainRejectedInput, BrainUnreachable
from richard.realtime import events
from richard.realtime.chunker import ProgressiveChunker
from richard.realtime.vad import FRAME_BYTES
from richard import vision

BRAIN_DOWN_LINE = "I can't reach my brain right now."
TURN_FAILED_LINE = "Something went wrong on my end."
IMAGE_REJECTED_LINE = "I couldn't take that picture in."
NO_CLIENT_RESULT = "no result from client"
```

In `__init__`, after `self.barge_in = barge_in`:

```python
        # Tools the connected client owns (the Reachy app's camera, the browser's
        # webcam), as chat-completions schemas. The engine defers their calls to us.
        self.client_tools: list[dict] = []
        self.dropped_tools: list[str] = []
```

Replace `create_text_item` and `update` with:

```python
    def create_item(self, item: dict) -> None:
        """Append a parsed conversation item (see events.parse_item). Append only: no turn
        starts until response.create. Raises ValueError when the item cannot be appended."""
        if item["kind"] == "message":
            content = item["content"]
            self.conversation.add_user(content)
            urls = vision.image_urls(content)
            if urls:
                total = vision.count_images(self.conversation.history())
                for url in urls:
                    mime, nbytes = vision.check_image_data_url(url)
                    vision.log_image("client", mime, nbytes, total)
            return
        if item["kind"] == "function_call_output":
            if item["call_id"] not in self.conversation.pending_client_calls():
                raise ValueError(f"function_call_output: unknown or already answered call_id {item['call_id']}")
            self.conversation.add_tool_result(item["call_id"], item["output"])
            return
        raise ValueError(f"unsupported item kind: {item['kind']}")

    def create_response(self) -> None:
        """Run a turn on the conversation as it stands (GA semantics)."""
        if self.state != "listening":
            self._emit(events.error("conversation already has an active response",
                                    code=events.ACTIVE_RESPONSE_CODE))
            return
        history = self.conversation.history()
        nothing_new = not history or (
            history[-1].role == "assistant" and not self.conversation.pending_client_calls()
        )
        if nothing_new:
            response_id = events.new_id("resp")
            self._emit(events.response_created(response_id))
            self._emit(events.response_done(response_id))
            return
        self._start_turn(None)

    def cancel_response(self) -> None:
        self._interrupt.set()

    def update(self, patch: dict) -> dict:
        if patch.get("barge_in") in ("vad", "wake", "off"):
            self.barge_in = patch["barge_in"]
        if "tools" in patch:
            reserved = set(getattr(self._engine, "tool_names", lambda: [])())
            self.client_tools, self.dropped_tools = events.tools_to_schemas(patch["tools"], reserved)
        return {
            "barge_in": self.barge_in,
            "tools": [s["function"]["name"] for s in self.client_tools],
            "dropped_tools": list(self.dropped_tools),
        }
```

Replace `_start_turn` and `_respond` with:

```python
    def _start_turn(self, get_transcript, *, announce_transcript: bool = True) -> None:
        """Run a turn on its own thread. `get_transcript` yields the new user text, or is
        None for a response.create on the conversation as it stands."""
        previous = self._turn_thread
        item_id = self._input_item_id
        self.state = "thinking"  # claimed now, so a second response.create sees it active

        def run() -> None:
            if previous is not None:
                previous.join(timeout=10.0)  # let an interrupted turn finish truncating
            self._interrupt.clear()  # anything set before this belonged to the previous turn
            self.state = "thinking"
            if get_transcript is None:
                self._respond(None)
                return
            try:
                transcript = get_transcript()
            except Exception as exc:
                self._emit(events.error(f"transcription failed: {exc}", code="stt_error"))
                self.state = "listening"
                return
            if not transcript:
                self.state = "listening"
                return
            if announce_transcript:
                self._emit(events.transcription_completed(item_id, transcript))
            self._respond(transcript)

        self._turn_thread = threading.Thread(target=run, daemon=True)
        self._turn_thread.start()

    def _respond(self, user_text: str | None) -> None:
        # A call the client never answered would leave a tool call without a result in
        # the served prefix; seal it so the model sees the failure instead of a broken prompt.
        self.conversation.seal_pending(NO_CLIENT_RESULT)
        if user_text is not None:
            self.conversation.add_user(user_text)
        response_id = events.new_id("resp")
        self._emit(events.response_created(response_id))
        chunker = ProgressiveChunker()
        full = ""
        status = "completed"
        handed_to_client = False
        try:
            for delta in self._engine.respond_streaming(self.conversation, client_tools=self.client_tools):
                if self._interrupt.is_set():
                    status = "cancelled"
                    break
                if isinstance(delta, ClientToolCall):
                    # The engine recorded the call and ends the turn; the client runs the
                    # tool and asks for a new response. Speak what was said before it.
                    handed_to_client = True
                    tail = chunker.flush()
                    if tail and not self._speak(response_id, tail):
                        status = "cancelled"
                        break
                    vision.log_client_call(delta.name, delta.arguments)
                    self._emit(events.function_call_arguments_done(
                        response_id, delta.id, delta.name, delta.arguments))
                    continue
                full += delta
                self._emit(events.text_delta(response_id, delta))
                if not all(self._speak(response_id, c) for c in chunker.feed(delta)):
                    status = "cancelled"
                    break
            if status == "completed" and not handed_to_client:
                tail = chunker.flush()
                if tail and not self._speak(response_id, tail):
                    status = "cancelled"
        except BrainRejectedInput as exc:
            status = "failed"
            self._emit(events.error(str(exc), code="brain_rejected_input"))
            self._speak(response_id, IMAGE_REJECTED_LINE)
        except BrainUnreachable:
            status = "failed"
            self._emit(events.error("brain unreachable", code="brain_unreachable"))
            self._speak(response_id, BRAIN_DOWN_LINE)
        except Exception as exc:
            status = "failed"
            self._emit(events.error(f"engine failed: {exc}", code="engine_error"))
            # Audible failure: silence here reads as a crash to the user.
            self._speak(response_id, TURN_FAILED_LINE)
        # A turn handed to the client already stored its text inside the tool-call
        # message; storing it again would put a plain reply after the call.
        if full and not handed_to_client:
            self.conversation.add_assistant(full)
        if status == "cancelled":
            self._emit(events.item_truncated(response_id))
        # Listening again BEFORE response.done goes out: the Reachy app posts the tool
        # output and response.create the moment it sees response.done, and must not be
        # told the response is still active.
        self.state = "listening"
        self._emit(events.response_done(response_id, status))
```

Note for the implementer: `_handle_frame` still calls `self._start_turn(lambda: self._transcriber.final(pcm))`; unchanged. The engine's `respond_streaming` must accept the `client_tools` keyword; the test fakes in this file (`FakeEngine`, `DownEngine`) must gain `client_tools=None` in their signatures. The `state = "listening"` before `response.done` ordering is load-bearing for the round-trip test (it calls `create_response()` right after `wait(done)`).

- [ ] **Step 4: Run the tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_realtime_session.py -q`
Expected: all pass, including the existing barge-in tests.

- [ ] **Step 5: Commit**

```bash
git add src/richard/realtime/session.py tests/test_realtime_session.py
git commit -m "realtime session: client tools, append-only items, response.create, client-call turns

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 9: Server dispatch of the new events

**Files:**
- Modify: `src/richard/realtime/server.py`
- Test: `tests/test_realtime_server.py`

**Interfaces:**
- Consumes: `events.parse_item`, `session.create_item`, `session.create_response`.

- [ ] **Step 1: Write the failing tests**

In `tests/test_realtime_server.py`, change `FakeSession`: replace `self.texts = []` / `create_text_item` with:

```python
        self.items = []
        self.responses = 0

    def create_item(self, item):
        if item.get("kind") == "function_call_output" and item["call_id"] == "unknown":
            raise ValueError("function_call_output: unknown or already answered call_id unknown")
        self.items.append(item)

    def create_response(self):
        self.responses += 1
```

Update `test_audio_and_text_and_cancel_are_dispatched` to assert `session.items == [{"kind": "message", "content": "hi"}]` instead of `session.texts == ["hi"]`, and `test_non_dict_item_create_gets_error_and_survives` to assert `session.items == []`. Then append:

```python
IMG = "data:image/jpeg;base64,/9j/4AAQ"


def test_response_create_and_function_call_output_are_dispatched():
    ws = FakeWs([
        json.dumps({"type": "conversation.item.create", "item": {
            "type": "function_call_output", "call_id": "c1", "output": '{"image_attached": true}'}}),
        json.dumps({"type": "conversation.item.create", "item": {
            "type": "message", "role": "user", "content": [{"type": "input_image", "image_url": IMG}]}}),
        json.dumps({"type": "response.create"}),
    ])
    (session,) = run(ws)
    assert session.items[0] == {"kind": "function_call_output", "call_id": "c1", "output": '{"image_attached": true}'}
    assert session.items[1] == {"kind": "message", "content": [{"type": "image_url", "image_url": {"url": IMG}}]}
    assert session.responses == 1
    assert not any(m["type"] == "error" for m in ws.sent)


def test_bad_items_get_invalid_request_and_the_connection_survives():
    ws = FakeWs([
        json.dumps({"type": "conversation.item.create", "item": {
            "type": "message", "content": [{"type": "input_image", "image_url": "http://x/y.jpg"}]}}),
        json.dumps({"type": "conversation.item.create", "item": {
            "type": "function_call_output", "call_id": "unknown", "output": "{}"}}),
        _append(b"\x03"),
    ])
    (session,) = run(ws)
    errors = [m for m in ws.sent if m["type"] == "error"]
    assert [e["error"]["code"] for e in errors] == ["invalid_request", "invalid_request"]
    assert "data:image" in errors[0]["error"]["message"]
    assert session.items == []
    assert session.audio == b"\x03"


def test_session_update_with_tools_is_passed_through():
    ws = FakeWs([json.dumps({"type": "session.update", "session": {
        "type": "realtime", "tools": [{"type": "function", "name": "camera", "parameters": {}}]}})])
    (session,) = run(ws)
    updated = [m for m in ws.sent if m["type"] == "session.updated"]
    assert updated  # the fake echoes barge_in; the real session's tool handling is tested in test_realtime_session
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_realtime_server.py -q`
Expected: FAIL (`create_text_item` called on the fake; `response.create` not dispatched).

- [ ] **Step 3: Implement**

In `src/richard/realtime/server.py`, delete `_text_content` and replace the `conversation.item.create` branch in `handle_realtime` with:

```python
                elif etype == "conversation.item.create":
                    item = event.get("item")
                    if not isinstance(item, dict):
                        emit(events.error("conversation.item.create: 'item' must be an object"))
                        continue
                    try:
                        session.create_item(events.parse_item(item))
                    except ValueError as exc:
                        emit(events.error(str(exc)))
                elif etype == "response.create":
                    session.create_response()
```

Update the module docstring's inbound line to list `response.create`.

- [ ] **Step 4: Run the tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_realtime_server.py tests/test_realtime_session.py -q`
Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add src/richard/realtime/server.py tests/test_realtime_server.py
git commit -m "realtime server: dispatch response.create and parsed items (message, function_call_output)

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 10: Web app: content parts on `/api/chat` and `/api/voice`

**Files:**
- Modify: `src/richard/web/app.py`
- Test: `tests/test_web.py`

**Interfaces:**
- Produces: `_user_content(content) -> str | list[dict]` (raises `ValueError`); `_conversation_from_messages(messages)` accepts parts (raises `ValueError`); `_serve_chat` and `_serve_voice` answer 400 on bad content; `_chat_sse_events` and `_voice_turn` speak `IMAGE_REJECTED_LINE` on `BrainRejectedInput`.
- Consumes: `vision.check_image_data_url`, `vision.count_images`, `conversation.user_parts`, `Message.text()`, `errors.BrainRejectedInput`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_web.py`:

```python
# --- vision: content parts on chat and voice ---

_IMG = "data:image/jpeg;base64,/9j/4AAQ"


def test_conversation_from_messages_accepts_parts_and_validates_images():
    from richard.web.app import _conversation_from_messages
    convo = _conversation_from_messages([
        {"role": "user", "content": [{"type": "text", "text": "what is this?"},
                                     {"type": "image_url", "image_url": {"url": _IMG}}]},
        {"role": "assistant", "content": "A mug."},
        {"role": "user", "content": [{"type": "image_url", "image_url": {"url": _IMG}}]},
    ])
    hist = [m.to_chat() for m in convo.history()]
    assert hist[0]["content"] == [{"type": "text", "text": "what is this?"},
                                  {"type": "image_url", "image_url": {"url": _IMG}}]
    assert hist[1] == {"role": "assistant", "content": "A mug."}
    assert hist[2]["content"] == [{"type": "image_url", "image_url": {"url": _IMG}}]


@pytest.mark.parametrize("content,fragment", [
    ([{"type": "image_url", "image_url": {"url": "http://x/y.jpg"}}], "data:image"),
    ([{"type": "audio", "data": "x"}], "unsupported"),
    (["not a part"], "objects"),
    (42, "string or a list"),
])
def test_conversation_from_messages_rejects_bad_content(content, fragment):
    from richard.web.app import _conversation_from_messages
    with pytest.raises(ValueError, match=fragment):
        _conversation_from_messages([{"role": "user", "content": content}])


def test_chat_endpoint_400_on_bad_image(tmp_path):
    config_path = tmp_path / "config.toml"
    save_config(Config(), config_path)
    app = WebApp(config_path=config_path, memory_store=MemoryStore(":memory:"), relays=RelayRegistry(),
                 engine_factory=lambda: _FakeEngine(["never"]))
    from richard.web.app import _serve_chat
    w = _FakeWriter()
    body = json.dumps({"messages": [{"role": "user", "content": [
        {"type": "image_url", "image_url": {"url": "data:image/gif;base64,R0lG"}}]}]}).encode()
    asyncio.run(_serve_chat(app, body, w))
    blob = b"".join(w.chunks)
    assert blob.startswith(b"HTTP/1.1 400")
    assert b"text/event-stream" not in blob


def test_chat_endpoint_streams_with_an_attached_image(tmp_path):
    config_path = tmp_path / "config.toml"
    save_config(Config(), config_path)
    seen = {}

    class Eng:
        def respond_streaming(self, conversation):
            seen["hist"] = [m.to_chat() for m in conversation.history()]
            yield "A mug."

    app = WebApp(config_path=config_path, memory_store=MemoryStore(":memory:"), relays=RelayRegistry(),
                 engine_factory=lambda: Eng())
    from richard.web.app import _serve_chat
    w = _FakeWriter()
    body = json.dumps({"messages": [{"role": "user", "content": [
        {"type": "text", "text": "what"}, {"type": "image_url", "image_url": {"url": _IMG}}]}]}).encode()
    asyncio.run(_serve_chat(app, body, w))
    assert 'data: {"delta": "A mug."}' in b"".join(w.chunks).decode()
    assert seen["hist"][0]["content"][1]["type"] == "image_url"


def test_chat_sse_events_rejected_image_yields_its_line():
    from richard.errors import BrainRejectedInput
    from richard.web.app import _chat_sse_events

    class Rejecting:
        def respond_streaming(self, conversation):
            raise BrainRejectedInput("400")
            yield  # pragma: no cover

    ev = list(_chat_sse_events(Rejecting(), [{"role": "user", "content": "hi"}]))
    assert ev[0] == 'data: {"error": "I couldn\'t take that picture in."}\n\n'
    assert ev[-1] == 'data: {"done": true}\n\n'


def test_voice_turn_carries_an_attached_image_before_the_transcript():
    from richard.web.app import _voice_turn
    eng = _RespEngine("A mug.")
    messages = [{"role": "user", "content": [{"type": "image_url", "image_url": {"url": _IMG}}]}]
    out = _voice_turn(lambda b: "what is this", eng, _FakeSynth(), messages, b"audio")
    assert out["reply"] == "A mug."
    hist = [m.to_chat() for m in eng.seen.history()]
    assert hist[0]["content"][0]["type"] == "image_url"
    assert hist[1] == {"role": "user", "content": "what is this"}


def test_voice_turn_rejected_image_line():
    from richard.errors import BrainRejectedInput
    from richard.web.app import _voice_turn

    class Rejecting:
        def respond(self, conversation):
            raise BrainRejectedInput("400")

    out = _voice_turn(lambda b: "look", Rejecting(), _FakeSynth(), [], b"x")
    assert out["reply"] == "I couldn't take that picture in."


def test_voice_endpoint_400_on_bad_image(tmp_path):
    config_path = tmp_path / "config.toml"
    save_config(Config(), config_path)
    app = WebApp(config_path=config_path, memory_store=MemoryStore(":memory:"), relays=RelayRegistry(),
                 voice_turn=lambda messages, audio: {"transcript": "x", "reply": "y", "audio": None})
    from richard.web.app import _serve_voice
    w = _FakeWriter()
    body = json.dumps({"messages": [{"role": "user", "content": [
        {"type": "image_url", "image_url": {"url": "nope"}}]}], "audio": ""}).encode()
    asyncio.run(_serve_voice(app, body, w))
    assert b"".join(w.chunks).startswith(b"HTTP/1.1 400")
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_web.py -q -k "vision or parts or image or rejected"`
Expected: FAIL (`_conversation_from_messages` does not validate; 400 paths missing).

- [ ] **Step 3: Implement**

In `src/richard/web/app.py`:

Imports:

```python
from richard import vision
from richard.conversation import Conversation, Message, user_parts
from richard.errors import BrainRejectedInput, BrainUnreachable

BRAIN_DOWN_LINE = "I can't reach my brain right now."
IMAGE_REJECTED_LINE = "I couldn't take that picture in."
```

Replace `_conversation_from_messages` with:

```python
def _user_content(content):
    """A string, or a list of chat-completions parts with validated images.

    Raises ValueError with a message fit for a 400.
    """
    if isinstance(content, str):
        return content
    if not isinstance(content, list):
        raise ValueError("message content must be a string or a list of parts")
    texts: list[str] = []
    images: list[str] = []
    for part in content:
        if not isinstance(part, dict):
            raise ValueError("content parts must be objects")
        if part.get("type") == "text" and isinstance(part.get("text"), str):
            texts.append(part["text"].strip())
        elif part.get("type") == "image_url":
            ref = part.get("image_url")
            url = ref.get("url") if isinstance(ref, dict) else None
            vision.check_image_data_url(url)
            images.append(url)
        else:
            raise ValueError("unsupported content part")
    text = " ".join(t for t in texts if t).strip()
    if not images:
        return text
    return user_parts(text or None, images)


def _conversation_from_messages(messages) -> Conversation:
    """Build the conversation the browser holds. Raises ValueError on bad content."""
    convo = Conversation()
    for m in messages or []:
        role, content = m.get("role"), m.get("content", "")
        if role == "user":
            convo.add_user(_user_content(content))
        elif role == "assistant":
            convo.add_assistant(content if isinstance(content, str) else Message("assistant", content).text())
    images = vision.count_images(convo.history())
    if images:
        vision.log_history("web", images)
    return convo
```

In `_voice_turn`, replace the `try/except BrainUnreachable` with:

```python
    try:
        reply = engine.respond(convo)
    except BrainRejectedInput:
        reply = IMAGE_REJECTED_LINE
    except BrainUnreachable:
        reply = BRAIN_DOWN_LINE
```

In `_chat_sse_events`, replace the `except BrainUnreachable` with:

```python
    except BrainRejectedInput:
        yield _sse({"error": IMAGE_REJECTED_LINE})
    except BrainUnreachable:
        yield _sse({"error": BRAIN_DOWN_LINE})
```

In `_serve_chat`, right after `messages = ...` is parsed and before the SSE headers are written:

```python
    try:
        _conversation_from_messages(messages)  # validate before committing to a 200 stream
    except ValueError as exc:
        writer.write(_format_response(Response.bad_request(str(exc)), False))
        await writer.drain()
        return
```

In `_serve_voice`, after `audio = ...` inside the same `try`, add `_conversation_from_messages(messages)` so a `ValueError` lands in the existing `except (ValueError, TypeError)` branch that already answers 400.

- [ ] **Step 4: Run the tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_web.py -q`
Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add src/richard/web/app.py tests/test_web.py
git commit -m "web: content parts on /api/chat and /api/voice, 400 on bad images, rejected-image line

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 11: Web UI: attach a picture in typed chat and push-to-talk

**Files:**
- Modify: `src/richard/web/static.py`
- Test: `tests/test_web.py`

**Interfaces:**
- Produces (JS): `shrinkImage(file, maxEdge, quality) -> Promise<{url, width, height}>`; `frameFromCanvasSource(source, srcW, srcH, maxEdge) -> {url, width, height} | null` (shared with Task 12); `pendingImage`; `contentText(c)`, `contentThumbs(c)`.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_web.py`:

```python
def test_home_page_has_the_attach_control_and_thumbnail_rendering(tmp_path):
    html = _app(tmp_path).handle("GET", "/").body.decode()
    assert 'id="chat-attach"' in html
    assert 'id="chat-image-file"' in html and 'accept="image/*"' in html and 'capture="environment"' in html
    assert 'id="chat-attach-chip"' in html
    assert "function shrinkImage(" in html
    assert "function contentThumbs(" in html
    assert "class=\"chat-thumb\"" in html or "class='chat-thumb'" in html or "chat-thumb" in html
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_web.py -q -k attach_control`
Expected: FAIL.

- [ ] **Step 3: Implement**

All edits are inside `SPA_HTML` in `src/richard/web/static.py`.

(a) CSS. After the line `.chat-empty { color: var(--text-secondary); font-style: italic; }` add:

```css
  .chat-thumb { display: block; max-width: 160px; max-height: 120px; border: var(--pixel-border); margin-top: 0.3rem; }
  .attach-chip {
    display: flex; align-items: center; gap: 0.5rem; margin-bottom: 0.35rem; padding: 0.3rem 0.5rem;
    border: var(--pixel-border); background: var(--surface-light); font-size: 0.8rem;
  }
  .attach-chip img { width: 36px; height: 36px; object-fit: cover; border: var(--pixel-border); }
  .attach-chip span { flex: 1; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
  .attach-chip button { background: none; border: 0; color: var(--text-primary); cursor: pointer; font-size: 1rem; }
```

Change the composer grid to five columns: `.composer { display: grid; grid-template-columns: minmax(0, 1fr) auto auto auto auto; }` and, in the `@media (max-width: 700px)` block, `.composer { grid-template-columns: minmax(0, 1fr) auto auto auto; }`.

(b) Markup. Replace the composer block with:

```html
    <div class="composer-wrap">
      <div class="attach-chip" id="chat-attach-chip" hidden>
        <img id="chat-attach-thumb" alt="">
        <span id="chat-attach-name"></span>
        <button type="button" id="chat-attach-clear" title="Remove the picture">×</button>
      </div>
      <div class="composer">
        <input id="chat-input" class="setting-input" placeholder="Ask Richard…" autocomplete="off">
        <button class="setting-button composer-send" id="chat-send" type="button">Send</button>
        <button class="setting-button composer-mic" id="chat-attach" type="button" title="Attach a picture">⊡</button>
        <button class="setting-button composer-mic" id="chat-mic" type="button" title="Hold to talk">●</button>
        <button class="setting-button composer-mic" id="voice-mode" type="button" title="Hands-free voice mode">◉</button>
      </div>
      <input type="file" id="chat-image-file" accept="image/*" capture="environment" hidden>
      <div class="status-line" id="chat-status"></div>
    </div>
```

(c) JS. Replace `renderChatHistory` and `sendChat` with:

```js
function contentText(c){
  if (typeof c === 'string') return c;
  return (c || []).filter(p => p && p.type === 'text').map(p => p.text).join(' ');
}
function contentThumbs(c){
  if (typeof c === 'string') return '';
  return (c || []).filter(p => p && p.type === 'image_url' && p.image_url && /^data:image\//.test(p.image_url.url))
    .map(p => '<img class="chat-thumb" src="' + p.image_url.url + '" alt="attached picture">').join('');
}
function renderChatHistory(){
  const log = $('chat-log');
  if (!chatHistory.length) {
    log.innerHTML = '<div class="chat-empty">No messages in this session.</div>';
    return;
  }
  log.innerHTML = chatHistory.map(message =>
    '<div class="chat-msg ' + (message.role === 'user' ? 'user' : 'richard') + '">' +
      '<span class="who">' + (message.role === 'user' ? 'you&gt;' : 'richard&gt;') + '</span>' +
      '<span class="body">' + esc(contentText(message.content)) + contentThumbs(message.content) + '</span></div>'
  ).join('');
  log.scrollTop = log.scrollHeight;
}

/* ---- pictures: resized in the browser (800 px long edge ≈ 474 prompt tokens, measured) ---- */
function frameFromCanvasSource(source, srcW, srcH, maxEdge, quality){
  if (!srcW || !srcH) return null;
  const scale = Math.min(1, maxEdge / Math.max(srcW, srcH));
  const w = Math.round(srcW * scale), h = Math.round(srcH * scale);
  const c = document.createElement('canvas'); c.width = w; c.height = h;
  c.getContext('2d').drawImage(source, 0, 0, w, h);
  return {url: c.toDataURL('image/jpeg', quality == null ? 0.85 : quality), width: w, height: h};
}
async function shrinkImage(file, maxEdge, quality){
  const bmp = await createImageBitmap(file);
  try {
    const shot = frameFromCanvasSource(bmp, bmp.width, bmp.height, maxEdge, quality);
    if (!shot) throw new Error('empty image');
    return shot;
  } finally { bmp.close(); }
}
let pendingImage = null;  // {url, width, height, name}
function showAttachChip(){
  const chip = $('chat-attach-chip');
  if (!pendingImage) { chip.hidden = true; return; }
  $('chat-attach-thumb').src = pendingImage.url;
  $('chat-attach-name').textContent = pendingImage.name + ' · ' + pendingImage.width + '×' + pendingImage.height;
  chip.hidden = false;
}
function takePendingImage(){ const img = pendingImage; pendingImage = null; showAttachChip(); return img; }
$('chat-attach').addEventListener('click', () => $('chat-image-file').click());
$('chat-attach-clear').addEventListener('click', () => { pendingImage = null; showAttachChip(); });
$('chat-image-file').addEventListener('change', async e => {
  const file = e.target.files && e.target.files[0]; e.target.value = '';
  if (!file) return;
  try { const shot = await shrinkImage(file, 800, 0.85); pendingImage = Object.assign(shot, {name: file.name || 'picture'}); showAttachChip(); setStatus('chat', '', ''); }
  catch (err) { setStatus('chat', 'picture failed: ' + err.message, 'err'); }
});

async function sendChat(){
  const input = $('chat-input'); const text = input.value.trim();
  const image = pendingImage;
  if (!text && !image) return;
  input.value = '';
  takePendingImage();
  const content = image
    ? (text ? [{type: 'text', text}, {type: 'image_url', image_url: {url: image.url}}] : [{type: 'image_url', image_url: {url: image.url}}])
    : text;
  chatHistory.push({role: 'user', content}); renderChatHistory();
  $('chat-send').disabled = true; $('chat-mic').disabled = true;
  setEntityState('thinking'); setLatestReply('', 'streaming'); setStatus('chat', 'thinking…', '');
  let reply = '';
  try {
    const r = await fetch('/api/chat', {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({messages: chatHistory})});
    if (!r.ok || !r.body) throw new Error('HTTP ' + r.status + (r.status === 400 ? ': ' + await r.text() : ''));
    const reader = r.body.getReader(); const dec = new TextDecoder(); let buf = '';
    for (;;) {
      const {value, done} = await reader.read();
      if (done) break;
      buf += dec.decode(value, {stream: true});
      let i;
      while ((i = buf.indexOf('\n\n')) >= 0) {
        const line = buf.slice(0, i); buf = buf.slice(i + 2);
        if (!line.startsWith('data:')) continue;
        const evt = JSON.parse(line.slice(5).trim());
        if (evt.delta) { reply += evt.delta; setLatestReply(reply, 'streaming'); }
        else if (evt.error) { reply = evt.error; setLatestReply(reply, 'error'); }
      }
    }
    chatHistory.push({role: 'assistant', content: reply});
    renderChatHistory();
    setLatestReply(reply);
    setStatus('chat', '', '');
  } catch (e) {
    setLatestReply(reply || ('Chat failed: ' + e.message), 'error');
    setStatus('chat', 'chat failed: ' + e.message, 'err');
  } finally {
    setEntityState('ready'); $('chat-send').disabled = false; $('chat-mic').disabled = false; input.focus();
  }
}
```

The existing `/api/chat` `fetch` error text for 400 must reach the user: keep the `r.status === 400` branch above.

(d) Push-to-talk. In `onRecordingStop`, right before `const data = await sendJSON('/api/voice', ...)`, add:

```js
    const image = takePendingImage();
    if (image) { chatHistory.push({role: 'user', content: [{type: 'image_url', image_url: {url: image.url}}]}); renderChatHistory(); }
```

Note: `chatHistory` now holds the image; the server sees it in `messages` before the transcript, as Task 10's `test_voice_turn_carries_an_attached_image_before_the_transcript` expects.

- [ ] **Step 4: Run the tests and check the page in a browser**

Run: `.venv/bin/python -m pytest tests/test_web.py -q`
Expected: all pass.

Then dump the page and open it locally (Chrome tools refuse `file://`):

```bash
.venv/bin/python -c "from richard.web.static import SPA_HTML; open('/tmp/richard-spa.html','w').write(SPA_HTML)"
(cd /tmp && python3 -m http.server 8765 --bind 127.0.0.1 >/dev/null 2>&1 &)
```

Open `http://127.0.0.1:8765/richard-spa.html?v=1` (bump `v` after every re-dump), attach a picture, confirm the chip shows the thumbnail and size, and that the composer keeps its alignment at 1200 px and 390 px widths. The API calls fail on the dump (no server); the visual check is what matters here.

- [ ] **Step 5: Commit**

```bash
git add src/richard/web/static.py tests/test_web.py
git commit -m "web ui: attach a picture to a typed or spoken turn, resized in the browser

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 12: Web UI: voice mode registers a `camera` tool and answers it from the webcam

**Files:**
- Modify: `src/richard/web/static.py`
- Test: `tests/test_web.py`

**Interfaces:**
- Consumes (JS): `frameFromCanvasSource`, `chatHistory`, `renderChatHistory`, `rt` state.
- Produces (JS): `CAMERA_TOOL`, `rtAnswerCall(call)`, `rt.video`, `rt.pendingCall`.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_web.py`:

```python
def test_voice_mode_registers_the_camera_tool_and_answers_calls(tmp_path):
    html = _app(tmp_path).handle("GET", "/").body.decode()
    assert "const CAMERA_TOOL" in html and "name: 'camera'" in html
    assert "'response.function_call_arguments.done'" in html
    assert "function rtAnswerCall(" in html
    assert "type: 'function_call_output'" in html and "type: 'input_image'" in html
    assert "type: 'response.create'" in html
    assert "enum: ['low', 'high']" in html
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_web.py -q -k camera_tool`
Expected: FAIL.

- [ ] **Step 3: Implement**

All edits inside `SPA_HTML`.

(a) Add after `let rtStarting = false;`:

```js
// The same tool the Reachy Conversation App offers on the robot, so this page exercises
// the robot's protocol path end to end: the brain decides to look, we take the frame.
const CAMERA_TOOL = {
  type: 'function', name: 'camera',
  description: 'Take a picture with the webcam to see what is in front of the computer: what the user is holding, how they look, the room. Use it when the user asks you to look at something, what you can see, or wants your visual opinion; if they ask you to look without saying at what, take the picture and describe what you see. Each call captures the current moment. Start with detail low; ask for high only to read text or see small objects.',
  parameters: {type: 'object', properties: {
    question: {type: 'string', description: 'What to observe or ask about in the picture.'},
    detail: {type: 'string', enum: ['low', 'high'], description: 'low (default) or high for small text and details.'}
  }, required: ['question']}
};
function rtSend(o){ if (rt && rt.ws.readyState === 1) rt.ws.send(JSON.stringify(o)); }
function rtAnswerCall(call){
  if (call.error){
    rtSend({type: 'conversation.item.create', item: {type: 'function_call_output', call_id: call.id, output: JSON.stringify({error: call.error})}});
    rtSend({type: 'response.create'}); return;
  }
  let detail = 'low';
  try { detail = JSON.parse(call.args || '{}').detail === 'high' ? 'high' : 'low'; } catch (_) {}
  const v = rt.video;
  const shot = v ? frameFromCanvasSource(v, v.videoWidth, v.videoHeight, detail === 'high' ? 1600 : 800) : null;
  if (!shot){
    rtSend({type: 'conversation.item.create', item: {type: 'function_call_output', call_id: call.id, output: JSON.stringify({error: 'No frame available'})}});
    rtSend({type: 'response.create'}); return;
  }
  chatHistory.push({role: 'user', content: [{type: 'image_url', image_url: {url: shot.url}}]}); renderChatHistory();
  rtSend({type: 'conversation.item.create', item: {type: 'function_call_output', call_id: call.id,
    output: JSON.stringify({image_attached: true, image_width: shot.width, image_height: shot.height})}});
  rtSend({type: 'conversation.item.create', item: {type: 'message', role: 'user', content: [{type: 'input_image', image_url: shot.url}]}});
  rtSend({type: 'response.create'});
}
```

(b) In `rtHandle`, change the `session.created` case and `response.done`, and add the function-call case:

```js
    case 'session.created':
      rt.outRate = msg.session.output_audio_samplerate;
      if (rt.video) rtSend({type: 'session.update', session: {tools: [CAMERA_TOOL]}});
      break;
    case 'response.function_call_arguments.done':
      rt.pendingCall = (msg.name === 'camera' && rt.video)
        ? {id: msg.call_id, args: msg.arguments}
        : {id: msg.call_id, error: 'unknown tool ' + msg.name};
      setEntityState('thinking'); setStatus('chat', 'looking…', '');
      break;
    case 'response.done':
      if (rt.replyLine){ chatHistory.push({role: 'assistant', content: rt.replyLine}); renderChatHistory(); setLatestReply(rt.replyLine); }
      if (rt.pendingCall){ const call = rt.pendingCall; rt.pendingCall = null; rtAnswerCall(call); break; }
      setEntityState('ready'); setStatus('chat', '', '');
      break;
```

(c) In `startVoiceMode`, replace the `getUserMedia({audio: ...})` call with a camera-first attempt and fallback, and keep the video element:

```js
      const audioSpec = {echoCancellation: true, noiseSuppression: true, channelCount: 1};
      try {
        stream = await navigator.mediaDevices.getUserMedia({audio: audioSpec, video: {width: {ideal: 1920}, height: {ideal: 1080}, facingMode: 'user'}});
      } catch (_) {
        stream = await navigator.mediaDevices.getUserMedia({audio: audioSpec});  // no camera: voice only
      }
```

After `rt = {ws, ctx, stream, node, playhead: 0, sources: [], outRate: 24000, userLine: '', replyLine: ''};` add:

```js
    rt.pendingCall = null; rt.video = null;
    if (stream.getVideoTracks().length){
      const v = document.createElement('video');
      v.muted = true; v.playsInline = true; v.autoplay = true; v.hidden = true;
      v.srcObject = new MediaStream(stream.getVideoTracks());
      document.body.appendChild(v);
      try { await v.play(); } catch (_) {}
      rt.video = v;
    }
```

The `session.created` message is replayed from `preMsgs` right after this block, so the `session.update` with the tool goes out once `rt.video` is known.

Change the status line `setStatus('chat', 'hands-free — just talk', '');` to `setStatus('chat', rt.video ? 'hands-free — just talk; Richard can look through the webcam' : 'hands-free — just talk', '');`.

(d) In `stopVoiceMode`, after `r.stream.getTracks().forEach(t => t.stop());` add `if (r.video) r.video.remove();`.

- [ ] **Step 4: Run the tests and check in a browser**

Run: `.venv/bin/python -m pytest tests/test_web.py -q`
Expected: all pass.

Visual check on the dump as in Task 11 (voice mode cannot connect on the dump; the check is that the page loads with no console errors: open DevTools console after `?v=2`).

- [ ] **Step 5: Commit**

```bash
git add src/richard/web/static.py tests/test_web.py
git commit -m "web ui: voice mode offers a camera tool and answers it from the webcam, the app's sequence

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 13: Docs, smoke script, full suite

**Files:**
- Modify: `docs/realtime-api.md`
- Create: `docs/vision.md`
- Modify: `README.md` (the "Realtime voice" bullet under "The rest of the companion", and one new "Vision" bullet after it)

- [ ] **Step 1: Update `docs/realtime-api.md`**

Client → server table becomes:

```markdown
| type | fields | effect |
|---|---|---|
| `input_audio_buffer.append` | `audio`: base64 PCM16 | feed microphone audio |
| `conversation.item.create` | `item`: `{type:"message", role:"user", content:[{type:"input_text",text} \| {type:"input_image",image_url}]}` or `{type:"function_call_output", call_id, output}` | append to the conversation; **no turn starts** |
| `response.create` | — | run a turn on the conversation as it stands; `error` `conversation_already_has_active_response` while a response is active; an empty response (`response.created` then `response.done`) when nothing is new |
| `response.cancel` | — | stop the in-flight response |
| `session.update` | `session.barge_in`: `"vad"\|"wake"\|"off"`; `session.tools`: flat function specs `{type:"function", name, description, parameters}` | change turn policy; register client-side tools (names owned by Richard's providers are dropped and listed in `session.updated.dropped_tools`); other keys are ignored |
```

Server → client table gains:

```markdown
| `response.function_call_arguments.done` | `call_id`, `name`, `arguments` (JSON string): the brain called one of the client's tools; run it, post `function_call_output` (and for a camera, an `input_image` message), then `response.create`. Followed by `response.done`. |
```

Add a section:

```markdown
## Client-side tools and pictures (the Reachy app's `camera`)

The sequence the Reachy Mini Conversation App uses, which the web UI's voice mode
reproduces from the webcam:

1. `session.update` with `tools: [{type:"function", name:"camera", ...}]`
2. the user speaks; the brain answers "Let me look." and calls `camera`
3. `response.function_call_arguments.done` `{call_id, name:"camera", arguments}` then `response.done`
4. client: `conversation.item.create` `{type:"function_call_output", call_id, output:"{\"image_attached\": true}"}`
5. client: `conversation.item.create` `{type:"message", role:"user", content:[{type:"input_image", image_url:"data:image/jpeg;base64,..."}]}`
6. client: `response.create` → the brain answers with the picture in view.

Images: `data:image/(jpeg|png|webp);base64,` up to 8 MB decoded; the server does not
resize (an 800 px long edge is about 474 prompt tokens on the production brain; a 720p
frame about 1200). A call the client never answers is sealed with `{"error": "no result
from client"}` before the next turn. A brain that rejects images answers `error`
`brain_rejected_input` and speaks "I couldn't take that picture in.".
```

Replace `response.audio.delta` in the "Not implemented" line: the list becomes "Out-of-band responses, `input_audio_buffer.commit` (server VAD only), audio output at rates other than the announced one." and remove "multi-modality" and "tool events".

- [ ] **Step 2: Write `docs/vision.md`**

```markdown
# Vision

Richard sees through pictures. Three paths, one core.

- **Typed chat and push-to-talk (web UI).** The ⊡ button attaches a picture (a phone opens
  its camera). The browser resizes it to an 800 px long edge (about 474 prompt tokens on the
  production brain) and sends it as an OpenAI content part on `/api/chat` or `/api/voice`.
  The page keeps the picture in its history, so later turns re-send it.
- **Voice mode (web UI).** The page offers a `camera` tool over `/v1/realtime`. When the brain
  decides to look it calls the tool; the page takes a webcam frame (800 px, or 1600 px when
  the brain asks for `detail: "high"`) and posts it exactly as the Reachy app does. Say
  "what am I holding?" and watch the entity switch to "looking…".
- **Robot.** The stock Reachy Mini Conversation App's `camera` tool follows the same sequence;
  see `docs/realtime-api.md`. The app sends its native frame (720p class, about 1200 tokens).

What Richard is told: he sees only through pictures, says so when he has none, and treats a
picture as one moment from one viewpoint (`richard/persona.py`, `PERCEPTION_RULES`).

Operations: images stay in the conversation for the session (append-only prompts keep the
prefix cache warm); two INFO lines on the `richard.vision` logger record each image and
each client tool call. A brain without vision answers 4xx: Richard says "I couldn't take
that picture in." and does not retry.

Next: the ambient layer (`docs/superpowers/specs/2026-09-09-ambient-perception-design.md`).
```

- [ ] **Step 2b: README**

In `README.md`, under "The rest of the companion", extend the **Realtime voice** bullet's
text with ", client-side tools and pictures (the Reachy Mini app's `camera`)" and add a bullet
right after it:

```markdown
- **Vision** — attach a picture in the web UI, or let Richard look through the webcam in
  voice mode; the brain sees the picture as an OpenAI content part. See [`docs/vision.md`](docs/vision.md).
```

- [ ] **Step 3: Note the CT123 smoke script change**

`/root/work/scripts/realtime_smoke.py` on CT123 (snapshot in `~/Documents/GitHub/inference-box/box/lxc123/work/scripts/realtime_smoke.py`) sends a typed `conversation.item.create` and expects a turn. After deploy it needs one more line after the item: `ws.send(json.dumps({"type": "response.create"}))`. Do that edit on CT123 at deploy time (not in this repo); add the reminder to the deploy checklist in the handoff.

- [ ] **Step 4: Run the full suite**

Run: `.venv/bin/python -m pytest -q`
Expected: everything green; count ≥ 595 + the new tests, 2 skipped, 3 deselected.

- [ ] **Step 5: Commit**

```bash
git add docs/realtime-api.md docs/vision.md README.md
git commit -m "docs: realtime item semantics, client tools and pictures; vision overview

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

## Deploy and live verification (after the plan, on Matteo's go)

Restarting `richard.service` drops his live session: ask first.

```bash
~/Documents/GitHub/inference-box/bin/rbox 'cd /opt/richard && git pull --ff-only && /root/.local/bin/uv pip install --python .venv/bin/python -e . && systemctl restart richard'
```

Then: (1) web chat with an attached picture ("what is this?"); (2) voice mode, "what am I holding?" → Richard says he will look, the entity shows "looking…", the answer describes the webcam frame; (3) `journalctl -u richard | grep 'vision:'` shows the image and client-call lines; (4) fix the CT123 smoke script (Task 13, step 3); (5) one dated line in `/root/CHANGELOG.md`, then `bin/pull-box-docs` and a path-scoped commit in `inference-box` (never `git add -A` there). The robot test waits for spec one.
