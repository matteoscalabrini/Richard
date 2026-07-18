# Richard's voice

**`richard-voice.wav`** — the reference clip Richard's voice is cloned from.

Richard speaks via **Chatterbox TTS** (Resemble AI), zero-shot voice cloning from a ~25 s
reference. Chatterbox runs as a server on the GPU host; clients call it over an
OpenAI-compatible `/v1/audio/speech` endpoint (`tts_engine = "remote"`). The active reference
is selected by the client's `tts_voice` config (the filename it sends in the `voice` field), so
swapping voices is a one-line `richard config --set-voice-name <file>.wav` — no server change.

Deployment: the installer copies `richard-voice.wav` into the Chatterbox sidecar's
`reference_audio/` directory. Chatterbox is the only external voice service; Silero VAD and
faster-whisper run inside `richard serve` for the realtime path.

Tuned delivery (deadpan, measured):
- `exaggeration ≈ 0.4` (lower = flatter)
- `cfg_weight ≈ 0.5` (lower = slower/more deliberate)

Voice cloning is GPU-only for the live loop (RTF is too high on CPU). Kokoro/Piper remain the
local CPU fallback (`tts_engine = "kokoro" | "piper"`).
