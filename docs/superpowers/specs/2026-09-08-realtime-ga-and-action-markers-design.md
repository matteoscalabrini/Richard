# Realtime GA protocol, inline action markers, server-initiated turns — design (spec one)

Status: approach approved by Matteo 2026-09-06/07 ("use the skeleton of Richard where possible,
and reframe it on GA realtime"; inline action markers instead of a tool round-trip; thinking as
knobs, not a ban). Depends on spec zero (plugins) only for where new providers are registered.
Voice-track work (Qwen3-TTS engine, x-vector cloning, task type) is already merged and out of scope.

## Goal

The stock Reachy Mini Conversation App, unmodified, running on the robot, connects to Richard's
`/v1/realtime` and gets: Richard's persona, memory and tools; speech in Richard's voice; robot
actions (dances, emotions, head moves, tracking) fired from Richard's replies; and the ability for
Richard to start speaking without being asked. Everything the app already does for the body
(motion layering, breathing, speech wobble, tool execution, profiles UI) stays on the Pi.

## What the app requires (from `huggingface_realtime.py`, openai SDK 2.28)

Client → server: `session.update` (type `realtime`, `instructions`, audio input/output format
`audio/pcm` with `rate: null` meaning 16 kHz, transcription language, `server_vad` with
`interrupt_response`, output `voice`, `tools` as flat function specs, `tool_choice`);
`input_audio_buffer.append` (base64 PCM16 16 kHz); `conversation.item.create` with
`input_text` messages, `function_call_output` items and `input_image` parts; `response.create`
with no body. It never sends `input_audio_buffer.commit` or `response.cancel`.

Server → client it acts on: `input_audio_buffer.speech_started/stopped`,
`conversation.item.input_audio_transcription.delta/completed`, `response.created`,
`response.output_text.delta/done`, `response.output_audio.delta/done`,
`response.output_audio_transcript.done`, `response.function_call_arguments.done`,
`response.done`, `error` with codes `conversation_already_has_active_response` and
`input_audio_buffer_commit_empty`. Unknown event types are ignored by the SDK's lenient parser.

Every one of the app's robot-action tools (`dance`, `play_emotion`, `move_head`,
`head_tracking`, `sweep_look`, `stop_*`, `go_to_sleep`) is `needs_response = False`: the app posts
the result and does not ask for a spoken follow-up.

## Design

### 1. One protocol: GA vocabulary and semantics

- Rename `response.audio.delta` → `response.output_audio.delta`; add `response.output_audio.done`,
  `response.output_text.done`, `response.output_audio_transcript.done`.
- `conversation.item.create` appends only. `response.create` starts a turn from the conversation.
  While a response is active, `response.create` answers `error` with code
  `conversation_already_has_active_response`; the app's sender loop retries on exactly that.
- Partial transcripts become append deltas: the suffix after the longest common prefix with the
  previous partial. Whisper partials can revise earlier words; documented best effort.
- The web UI client (`web/static.py`) moves to the same names and sends `response.create` after a
  typed item. `docs/realtime-api.md` is rewritten. No compatibility mode.

### 2. Session negotiation

- `tools`: stored per session. Name collisions with Richard providers: Richard wins, the duplicate
  is dropped and listed in `session.updated`. The app's `remember`/`forget` therefore never fire;
  memory stays in Richard.
- `instructions`: appended after the persona as a "Client instructions" section
  (`[realtime] client_instructions = "append" | "ignore"`, default append). The Reachy profile on
  the Pi is trimmed to body and tool description, not a second identity.
- Output rate negotiated from `audio.output.format.rate` (`null` → 16000); TTS output is
  resampled per session with the existing `resample_pcm16`; `session.created` announces it.
- `turn_detection.server_vad.interrupt_response` → `barge_in = "vad" | "off"`. Client `voice`
  ignored. Transcription `language` passed to the transcriber when Richard's language is `auto`.
- New `[realtime] tls` (default: follow web) because the Pi speaks plain `ws://` and the openai
  SDK refuses self-signed certificates. `[realtime] token` stays the auth (Bearer from the app's
  `HF_TOKEN`).

### 3. Inline action markers (no tool round-trip for robot actions)

Mechanism: the LLM writes `[verb:arg]` inside its reply text. A filter between the engine and the
TTS chunker holds back text from an opening bracket until the closing one (or a length cap of 40
chars), dispatches known verbs, and strips any short bracketed token so stage directions like
`[laughs]` are never spoken. Dispatch emits `response.function_call_arguments.done` with a
`call_id`, the tool `name` and JSON `arguments` built from the client's own schema, so the stock
app executes it. The action fires at the marker position, before the sentence is spoken.

- Vocabulary is derived from the session's `tools`: enums become the allowed args (`move_head`
  directions, dance names), booleans become `on|off`, zero-arg tools take no arg. Dance
  descriptions in the schema text ride into a compact system-prompt section: the verbs, the valid
  names, the syntax, and the rule "one action per bracket, at the point in the sentence where it
  should happen".
- Unknown verb or invalid arg: stripped, logged, not dispatched, one debug line; never spoken.
- `function_call_output` items are logged; an `error` output is surfaced to the model as a short
  user-role note on the next turn.
- Stored history keeps the raw text with markers so the model sees what it did.
- A parsed marker counts as an action for the engine's nudge logic (`_promises_action`), or "let
  me dance [dance:happy]" would buy the ACTION CHECK round we are avoiding.
- A `response.create` arriving with nothing new (only outputs of inline-dispatched calls) is
  answered with an empty response: `response.created` then `response.done`.
- Information tools (camera, weather, time, search on the Pi) are not markers. They are out of
  scope here: Richard's own providers cover time and weather server-side, camera is spec three.
  The `input_image` item is accepted and stored as a placeholder user message ("[image attached,
  not viewable by this backend]") so the model answers honestly.

### 4. Server-initiated turns (initiative needs them)

- `RealtimeSession.say(text, *, speak=True)` and `prompt(text)`: the first speaks a given line
  through TTS as a normal response (`response.created` … `response.done`) without an LLM call;
  the second appends an internal user-role item and runs a turn. Both refuse while a response is
  active (queue with a short TTL) and both mark the turn as unsolicited in events.
- A process-wide `SessionRegistry` exposes the open realtime sessions to the rest of `richard
  serve` (control-loop notifications, later spec four), keyed by client name from
  `session.update` when present.
- The Reachy app plays any response it receives; verified in its receive loop.

### 5. Brain knobs and telemetry

- Per brain role, an opaque `extra_body` merged into the chat-completions payload. Verified by
  Codex on the Qwen entry (2026-09-08): `{"chat_template_kwargs": {"enable_thinking": true,
  "reasoning_effort": "medium"}}` (template supports exactly `xhigh`, `medium`, `low`) and
  `{"enable_thinking": false}`. No numeric budget key is assumed.
- Streaming reader: `reasoning_content` deltas are counted, never yielded; inline `<think>…</think>`
  in `content` is stripped as insurance and counted the same way.
- Per-turn telemetry line at INFO: STT ms, reasoning tokens, time to first content token, first
  TTS chunk ms, first audio delta ms, total. p50/p95 are computed offline from logs; no new store.

## Data flow of one spoken turn

audio frames → VAD/endpointing → `speech_stopped` → STT final → `transcription.completed` →
`response.created` → engine streams text → marker filter (dispatch `function_call_arguments.done`
as encountered) → chunker → TTS → `output_audio.delta` (resampled) … → `output_text.done`,
`output_audio_transcript.done`, `output_audio.done`, `response.done`. Barge-in unchanged: VAD
`speech_started` during thinking/speaking sets the interrupt; the response ends `cancelled`.

## Error handling

- Malformed client events: `error` `invalid_request`, connection survives (existing rule).
- Engine failure: spoken failure line, `response.done` `failed` (existing).
- Marker parser never raises into the turn; a broken filter degrades to "speak everything".
- Client tool errors do not stop the turn; they become the next-turn note.

## Testing

- Unit: events (new builders, parsers, `response.create` rejection), session (item.create does
  not respond, `response.create` does, empty-response path, resample path, say/prompt, unsolicited
  flag), marker filter (split across deltas, unknown verb, malformed bracket, length cap, stage
  direction stripping, nudge interaction, vocabulary from schemas), brain reader (reasoning deltas
  counted not yielded, `<think>` stripped, `extra_body` merged), telemetry line.
- Contract test: the real `openai` SDK realtime client against Richard's server in-process with
  fake engine, STT and TTS; sends the app's exact `session.update`, feeds audio, expects a
  function-call event at the marker position, posts `function_call_output`, expects 16 kHz audio
  and `response.done`. `openai` is added to the dev extra.
- Live: Reachy app on the Mac against the MuJoCo daemon and Richard on the box; then the robot.

## Out of scope

Client-tool round trips that return information (resumable engine); vision; memory; the
per-language voice selection; the satellite protocol.
