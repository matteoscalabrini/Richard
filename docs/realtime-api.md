# Richard Realtime API (/v1/realtime)

Richard's voice API: a WebSocket speaking a documented subset of the OpenAI
Realtime API event vocabulary. Any client that can stream PCM16 and parse JSON
events can hold a hands-free, interruptible conversation with Richard — the web
UI uses exactly this API, and it is the contract for the Reachy Mini Conversation App.

## Connection

    wss://<host>:8766/v1/realtime            # port: [realtime].port in config.toml
    wss://<host>:8766/v1/realtime?token=...  # when [realtime].token is set

Auth (only when a token is configured): `?token=` query parameter (browsers)
or `Authorization: Bearer <token>` header. Wrong/missing token → close 4001.
Any path other than `/v1/realtime` is closed with code 4004.
TLS mirrors the web UI: the same self-signed pair, plaintext ws:// if TLS is off.

## Prepared voice cues

The browser can load short, same-voice waiting cues from the web server without
starting TTS during a conversation. Prepare the bank explicitly after configuring
the voice:

    python -m richard.realtime.cues prepare
    python -m richard.realtime.cues prepare --config /path/to/config.toml

The cache is stored in `realtime-cues/` beside the selected config file (normally
`~/.richard/realtime-cues/`), never in the repository. Preparation uses the
configured TTS engine, voice, language controls, and voice effect. It is idempotent
while that complete bank still matches the configuration. Use `--force` after
replacing a voice sample under the same voice identifier.

`voice.language = "it"` or `"Italian"` selects the Italian catalog. `"en"`,
`"English"`, `"auto"`, or an empty setting selects English. An unsupported explicit
language prepares no clips, so Richard remains silent rather than speaking a
different language.

The web server exposes the current bank through read-only HTTP:

    GET /api/realtime/cues

The response is `{fingerprint, language, clips}`. Each clip has `id`, `phase`,
`text`, base64 PCM16 `audio`, and `sample_rate`. A missing, corrupt, incomplete, or
wrong-voice bank returns the current fingerprint and language with `clips: []`.
The endpoint never synthesizes audio or starts a background preparation job.

## Audio formats

- **Input:** PCM16, mono, 16 kHz, base64-encoded in `input_audio_buffer.append`.
  Any frame size; the server rebuffers internally.
- **Output:** PCM16, mono, at the rate announced in `session.created`
  (`session.output_audio_samplerate`, e.g. 24000). Play deltas back-to-back.

## Client → server events

| type | fields | effect |
|---|---|---|
| `input_audio_buffer.append` | `audio`: base64 PCM16 | feed microphone audio |
| `conversation.item.create` | `item`: `{type:"message", role:"user", content:[{type:"input_text",text} \| {type:"input_image",image_url}]}` or `{type:"function_call_output", call_id, output}` | append to the conversation; **no turn starts** |
| `response.create` | — | run a turn on the conversation as it stands; `error` with code `conversation_already_has_active_response` while a response is active; an empty response (`response.created` then `response.done`) when nothing is new |
| `response.cancel` | — | stop the in-flight response |
| `session.update` | `session.barge_in`: `"vad"\|"wake"\|"off"`; `session.tools`: flat function specs `{type:"function", name, description, parameters}` | change turn policy; register client-side tools (names owned by Richard's providers are dropped and listed in `session.updated.dropped_tools`); other keys are ignored |

## Server → client events

| type | meaning |
|---|---|
| `session.created` / `session.updated` | session id + audio rates / effective settings (`barge_in`, `tools`, `dropped_tools`) |
| `input_audio_buffer.speech_started` / `speech_stopped` | VAD boundary (UI: listening state) |
| `conversation.item.input_audio_transcription.delta` | **full partial transcript so far** (replace, not append — deviation from OpenAI) |
| `conversation.item.input_audio_transcription.completed` | final transcript of the user turn |
| `response.created` | Richard started thinking |
| `response.output_text.delta` | reply text as it streams |
| `response.audio.delta` | base64 PCM16 chunk of speech |
| `response.function_call_arguments.done` | `call_id`, `name`, `arguments` (JSON string): the brain called one of the client's tools; run it, post `function_call_output` (and for a camera, an `input_image` message), then `response.create`. Followed by `response.done`. |
| `conversation.item.truncated` | reply was interrupted; discard queued audio |
| `response.done` | `response.status`: `completed` \| `cancelled` \| `failed` |
| `error` | `error.code` ∈ `invalid_request`, `conversation_already_has_active_response`, `stt_error`, `tts_error`, `brain_unreachable`, `brain_rejected_input`, `engine_error`, `internal_error` |

Malformed-but-parseable events (e.g. non-object `session` or `item` fields in valid event types, a bad image, an unknown `call_id`) receive an `error` event with `code: invalid_request`, and the connection survives.

## Turn-taking

Server-side VAD endpoints an utterance after ~400 ms of trailing silence
(`voice.endpoint_silence_ms`). With `barge_in: "vad"` (default), speaking while
Richard talks cancels his reply (`conversation.item.truncated` + `response.done`
status `cancelled`) — use client echo cancellation. `"off"`: Richard is deaf
until `response.done`. `"wake"`: reserved for the Phase 2 wake-word stage;
currently behaves like `"off"`.

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

## Not implemented (deliberately)

Out-of-band responses, `input_audio_buffer.commit` (server VAD only), audio output at
rates other than the announced one.
