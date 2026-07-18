# Richard Realtime API (/v1/realtime)

Richard's voice API: a WebSocket speaking a documented subset of the OpenAI
Realtime API event vocabulary. Any client that can stream PCM16 and parse JSON
events can hold a hands-free, interruptible conversation with Richard — the web
UI uses exactly this API, and it is the contract for future apps.

## Connection

    wss://<host>:8766/v1/realtime            # port: [realtime].port in config.toml
    wss://<host>:8766/v1/realtime?token=...  # when [realtime].token is set

Auth (only when a token is configured): `?token=` query parameter (browsers)
or `Authorization: Bearer <token>` header. Wrong/missing token → close 4001.
Any path other than `/v1/realtime` is closed with code 4004.
TLS mirrors the web UI: the same self-signed pair, plaintext ws:// if TLS is off.

## Audio formats

- **Input:** PCM16, mono, 16 kHz, base64-encoded in `input_audio_buffer.append`.
  Any frame size; the server rebuffers internally.
- **Output:** PCM16, mono, at the rate announced in `session.created`
  (`session.output_audio_samplerate`, e.g. 24000). Play deltas back-to-back.

## Client → server events

| type | fields | effect |
|---|---|---|
| `input_audio_buffer.append` | `audio`: base64 PCM16 | feed microphone audio |
| `conversation.item.create` | `item.content[].{type:"input_text",text}` | typed turn (no STT) |
| `response.cancel` | — | stop the in-flight response |
| `session.update` | `session.barge_in`: `"vad"\|"wake"\|"off"` | change turn policy (Phase 1: only `barge_in` honored; reply echoes effective settings) |

## Server → client events

| type | meaning |
|---|---|
| `session.created` / `session.updated` | session id + audio rates / effective settings |
| `input_audio_buffer.speech_started` / `speech_stopped` | VAD boundary (UI: listening state) |
| `conversation.item.input_audio_transcription.delta` | **full partial transcript so far** (replace, not append — deviation from OpenAI) |
| `conversation.item.input_audio_transcription.completed` | final transcript of the user turn |
| `response.created` | Richard started thinking |
| `response.output_text.delta` | reply text as it streams |
| `response.audio.delta` | base64 PCM16 chunk of speech |
| `conversation.item.truncated` | reply was interrupted; discard queued audio |
| `response.done` | `response.status`: `completed` \| `cancelled` \| `failed` |
| `error` | `error.code` ∈ `invalid_request`, `stt_error`, `tts_error`, `brain_unreachable`, `engine_error`, `internal_error` |

Malformed-but-parseable events (e.g. non-object `session` or `item` fields in valid event types) receive an `error` event with `code: invalid_request`, and the connection survives.

## Turn-taking

Server-side VAD endpoints an utterance after ~400 ms of trailing silence
(`voice.endpoint_silence_ms`). With `barge_in: "vad"` (default), speaking while
Richard talks cancels his reply (`conversation.item.truncated` + `response.done`
status `cancelled`) — use client echo cancellation. `"off"`: Richard is deaf
until `response.done`. `"wake"`: reserved for the Phase 2 wake-word stage;
currently behaves like `"off"`.

## Not implemented (deliberately)

Tool events (tools run inside Richard's engine, invisible here), multi-modality,
out-of-band responses, `input_audio_buffer.commit` (server VAD only).
