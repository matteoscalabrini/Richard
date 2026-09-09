# Vision: multimodal messages, camera tool over realtime, web attach — design (spec 3a)

Status: approach approved by Matteo 2026-09-09 ("via the web UI, but also as a capability usable
by Reachy when it is here"; three-stage perception confirmed the same evening: the ambient layer
consumes video, the brain consumes snapshots; the brain may ask for a higher-resolution frame when
it judges it necessary). Depends on spec zero (plugins, built). Takes the item/tool subset of spec
one early (see "Relation to spec one"). The ambient layer is spec 3b
(`2026-09-09-ambient-perception-design.md`); this spec is its fovea.

## Goal

Richard sees. Three paths, one core:

1. A picture attached in the web UI during a typed or push-to-talk turn.
2. In browser voice mode, the brain decides to look: it calls a `camera` tool, the page takes a
   webcam frame, the brain answers with the picture in front of it.
3. On the robot, the stock Reachy Mini Conversation App does exactly what the browser does in (2):
   the app's `camera` tool is model-initiated, runs on the Pi, and returns the frame through the
   OpenAI Realtime protocol. Richard must speak that protocol subset.

The browser path (2) is deliberately byte-for-byte the app's sequence, so the robot path is
exercised end to end before the robot exists.

## Facts that decide the design (measured or read from source)

- The production brain sees: `Qwen3.8-Flash-Next` on llama-swap accepts OpenAI `image_url`
  base64 data URIs. Measured 2026-09-06: an 800x500 image costs 474 prompt tokens; a photo
  question took 15.6 s for 696 prompt + 359 completion tokens with reasoning. The cost is the
  answer's decode, not the image. Prompts must stay append-only: an image in history is a cheap
  prefix hit each turn; a rewritten prefix is a full miss (~2 s measured on the hybrid entries).
- The app's camera sequence (`huggingface_realtime.py` ~L600-690, `tools/camera.py`): the model
  calls a function named `camera` with `{"question": ...}`; the app grabs
  `reachy_mini.media.get_frame_jpeg()` (full sensor frame, 1280x720 class, no resize); waits for
  `response.done`; sends `conversation.item.create` `{"type": "function_call_output", "call_id",
  "output": "{\"image_attached\": true}"}` (the base64 is stripped from the model-visible result);
  then `conversation.item.create` with a user `message` whose content is
  `[{"type": "input_image", "image_url": "data:image/jpeg;base64,..."}]`; then `response.create`
  with no body. `camera` has `needs_response = True`, so the `response.create` always follows.
  The app retries `response.create` on the error code `conversation_already_has_active_response`.
- The app's tools arrive in `session.update` as flat function specs
  `{"type": "function", "name", "description", "parameters"}` with `tool_choice: "auto"`.
- Richard today: `Message.content` is a string; `conversation.item.create` with text starts a
  turn immediately; `response.create`, `function_call_output` and `input_image` are rejected as
  unknown; the engine executes every tool call itself and never yields a call to the client.
- Richard's core has no image decoder (numpy only). Nothing in this spec decodes an image: the
  server validates the data URL header and size and passes bytes through.

## Design

### 1. Multimodal messages in the core

- `Message.content: str | list[dict] | None`. Parts use the chat-completions shape:
  `{"type": "text", "text": ...}` and `{"type": "image_url", "image_url": {"url": "data:image/jpeg;base64,..."}}`.
  `to_chat()` passes parts through unchanged. `Message.text()` returns the text of a message
  (string content, or the joined text parts, or "") for logs, the action-promise regex, the web
  history mirror and tests.
- `Conversation.add_user(content)` accepts a string or a list of parts. A helper
  `user_parts(text, image_urls)` builds the list (text part first when text is given, then one
  image part per URL).
- `Conversation.pending_client_calls()` returns the ids of tool calls in the last assistant
  tool-call message that have no tool result yet. `Conversation.seal_pending(reason)` appends a
  tool result `{"error": reason}` for each of them, so the served prefix is always well formed.
  Both are computed from history; no new state.
- `LlamaCppBrain.complete/stream/chat`: no change. A test asserts the exact payload carries the
  parts unchanged.
- Image validation lives in one helper `richard.vision.check_image_data_url(url) -> (mime, nbytes)`:
  scheme `data:image/(jpeg|png|webp);base64,`, base64 decodes, decoded size ≤ 8 MB (the
  `/api/voices` cap). Used by the web app and the realtime event parser; bad images are rejected at
  the edge and never reach the conversation.
- Brain rejection: if the brain answers 4xx to a request whose messages contain image parts, the
  client raises `BrainRejectedInput` (subclass of `BrainUnreachable`) with the response body in the
  log. The session and the web chat speak "I couldn't take that picture in." instead of the
  brain-down line, and do not retry.

### 2. Engine: client-side tools

- `Engine.respond_streaming(conversation, client_tools=None)`. `client_tools` are OpenAI function
  schemas the caller (the realtime session) owns but cannot execute server-side. They are merged
  into the schemas passed to the brain after the providers' own; a client tool whose name collides
  with a provider tool is dropped (Richard wins, spec one's rule); the session reports the drop.
- When a completion carries tool calls, the engine records the assistant tool-call message as
  today, executes the provider-owned calls and appends their results, and for each client-owned
  call yields a `ClientToolCall(id, name, arguments_json)` instead of executing it, then ends the
  turn. The yield type of `respond_streaming` becomes `str | ClientToolCall`; only callers that
  pass `client_tools` ever receive one. A client call counts as an action for the nudge logic.
- Text streamed before the call is yielded and stored as the tool-call message's content, so "let
  me look" is spoken and then the brain looks.
- `Engine.respond()` (used by `/api/voice`, control loops, the REPL) does not take client tools.
  Client tools are a realtime-session feature.
- The next turn is a normal turn on the appended history. There is no suspended generator and no
  per-session engine state: after the client posts the tool result and the image, the served
  prompt is `[..., assistant tool_call camera, tool result, user image]` and the brain answers.

### 3. Realtime protocol: the item/tool subset, GA semantics

Client → server (new or changed):

| event | handling |
|---|---|
| `session.update` with `session.tools` | flat function specs → chat schemas, stored per session; unknown keys the app sends (`type`, `instructions`, `audio`, `tool_choice`) are ignored, not errors. `session.updated` echoes `tools` (names kept) and `dropped_tools` (collisions). |
| `conversation.item.create`, item `message` (role user) | content parts `input_text` and `input_image` (data URL validated) → `Conversation.add_user(parts)`; a plain text-only item stays a string message. Append only: no turn starts. An item with no usable content → `error invalid_request`. |
| `conversation.item.create`, item `function_call_output` | `call_id` must be pending; `output` (string) becomes the tool result. Unknown or already answered `call_id` → `error invalid_request`, nothing appended. |
| `response.create` | starts a turn on the conversation as it stands. While a response is active → `error` with code `conversation_already_has_active_response` (the app retries on exactly that). If nothing is new since the last assistant reply → an empty response (`response.created` then `response.done`, status `completed`). Dangling client calls are sealed with `{"error": "no result from client"}` first. |

Server → client (new): `response.function_call_arguments.done`
`{"response_id", "call_id", "name", "arguments"}` when the engine yields a `ClientToolCall`,
followed by `response.done` (status `completed`). The app waits for that `response.done` before
posting the output.

Removed behaviour: a text item no longer runs a turn by itself. The browser voice mode never used
it; the CT123 smoke scripts (`realtime_smoke.py`, `realtime_spoken*.py`) get one `response.create`
line. `docs/realtime-api.md` is updated; no compatibility mode (spec one's rule).

A new user turn from speech seals dangling client calls before the transcript is appended, so a
user who talks over a pending camera call never produces a malformed prefix.

### 4. Web UI

- **Typed chat.** A camera button in the composer opens a hidden `<input type="file"
  accept="image/*" capture="environment">` (a phone opens its camera; a desktop opens a file
  picker). The image is resized in a canvas to an 800 px long edge, JPEG quality 0.85 (the
  measured 474-token size), shown as a thumbnail chip in the composer and beside the message in
  the log, and sent as content parts in `messages` on `/api/chat`. `chatHistory` keeps the parts,
  so later turns re-send the image (~60-100 KB base64 per image per request on the LAN; the
  server is stateless per request today and stays so).
- **Push-to-talk.** `/api/voice` accepts the same `messages`; an attached image is consumed by the
  next turn whether typed or spoken. No new UI.
- **Voice mode (realtime).** At start the page asks for microphone and camera together; if the
  camera is refused it falls back to audio only and registers no tool. With a camera it sends
  `session.update` with one tool, `camera`, whose schema mirrors the app's (`question`, required)
  plus `detail: "low" | "high"` (default low). On `response.function_call_arguments.done` with name
  `camera`, the page grabs a frame from the live video element at 800 px (low) or 1600 px (high)
  long edge, waits for `response.done`, posts `function_call_output` `{"image_attached": true,
  "image_width", "image_height"}`, posts the `input_image` item, sends `response.create`. Any other
  tool name gets `{"error": "unknown tool"}` and `response.create`. The entity shows a "looking"
  state between the call and the answer.
- Server side, `_conversation_from_messages` accepts string or parts and validates image parts
  (400 on a bad one). The JS log renders a message's text plus thumbnails.

### 5. Resolution on request

The brain chooses the detail it needs; the source's native resolution is the ceiling.

- Browser stand-in: `detail: "high"` = 1600 px long edge, roughly four times the tokens of low
  (~1900 vs 474). Rare by construction: the tool description says to use it only to read text or
  small objects.
- Robot through the stock app: the app's schema has only `question` and it always sends the full
  sensor frame (1280x720 class, ~1200 tokens). That is already the maximum the source can give.
  Serving low detail by default and a native-resolution crop on request from the robot is spec 3b's
  server-side camera provider, which owns the frame stream and an image codec.

### 6. Perception line in the system head

`persona.py` gains a static `PERCEPTION_RULES` paragraph appended after `ACTION_RULES`: Richard
sees only through pictures, an image attached to a message or a frame taken by calling a camera
tool when one is offered; with neither he says he cannot see right now and never describes a scene
he has not been shown; a picture shows one moment from one viewpoint. Static text keeps the head
byte-stable per conversation (pinned head). The tool's own description tells the model when to
look; no per-session prompt section is needed, so tools may arrive after the head is pinned.

### 7. Telemetry

One INFO line when an image enters a conversation (`vision: image source=attachment|camera_tool
mime=... bytes=... history_images=N`) and one when a client tool call is emitted (`vision: client
tool call name=camera detail=...`). p50/p95 stay offline from logs, as in spec one.

## Data flow of one robot (or browser voice) look

user speaks → transcript → `response.created` → engine streams "Let me look." → brain emits
tool call `camera` → engine records the tool-call message, yields `ClientToolCall` →
`response.function_call_arguments.done` → `response.done` → client grabs a frame →
`function_call_output` (tool result appended) → `input_image` (user image appended) →
`response.create` → new turn on the appended history → brain answers with the picture in context →
text and audio deltas → `response.done`.

## Error handling

- Bad image (scheme, base64, size): `error invalid_request` on the socket, 400 on the web; the
  connection and the conversation survive; nothing is appended.
- Brain 4xx on a request with images: `BrainRejectedInput`, spoken line, no retry, body logged.
- Client never posts the tool output: sealed with an error result at the next `response.create`
  or the next speech turn; the model sees the failure and says so.
- Unknown tool name from the brain that is neither a provider's nor a client's: today's "Unknown
  tool" result, unchanged.
- `response.create` during an active response: the documented error; the app retries.

## Testing

- Unit: `Message.text()`, `to_chat()` passthrough of parts, `user_parts`, `pending_client_calls`
  and `seal_pending`; brain client payload with parts unchanged and `BrainRejectedInput` on 4xx;
  engine client-tool deferral (fake brain: mixed provider + client calls in one round, text before
  the call, collision drop, nudge not triggered); `check_image_data_url` accept/reject table.
- Events: parsing of `session.update.tools`, `function_call_output`, `input_image`,
  `response.create`; builders for `function_call_arguments.done` and the active-response error.
- Session: the app's exact sequence with a scripted engine (event order asserted: created, text
  delta, function_call_arguments.done, done; then output + image + response.create → second turn
  sees `[user, assistant tool_call, tool, user parts]`); dangling call sealed by speech and by
  `response.create`; empty `response.create`; error while active.
- Server: `handle_realtime` with a fake socket replaying the same sequence; malformed items keep
  the connection.
- Web: `_conversation_from_messages` with parts and rejection; `/api/chat` SSE with an image;
  `/api/voice` with an attached image.
- Live (Matteo's go, CT123): a picture from the web UI in typed chat; voice mode "what am I
  holding?" → Richard calls `camera`, the browser posts the webcam frame, Richard answers. The
  robot after spec one (it cannot hear Richard until the audio vocabulary lands).

## Relation to spec one

Taken from spec one now, unchanged in meaning: `session.update` tools with the collision rule,
`conversation.item.create` as append-only, `response.create` as the turn trigger, the
`conversation_already_has_active_response` error, `function_call_output` and `input_image` items,
`response.function_call_arguments.done`. Left to spec one: the audio event rename and 16 kHz
negotiation, `instructions` merging, TLS toggle, inline action markers, server-initiated turns,
brain knobs in the UI, the per-turn latency line, the contract test with the real `openai` SDK.
Spec one's placeholder for `input_image` ("not viewable by this backend") is superseded: the image
is viewable.

## Out of scope

The ambient layer, frame streams, server-side camera provider and region crops (spec 3b); memory
provenance "observed" (spec two's schema; not faked with a prompt convention); image eviction or
summarising in history (rewrites the prefix; revisit with measurements); server-side resizing
(needs an image codec; spec 3b's extra); client tools on `/api/chat` (a second request round;
the browser voice mode covers model-initiated looking); the robot's head tracking (local to the
app).
