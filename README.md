<p align="center">
  <img src="https://raw.githubusercontent.com/matteoscalabrini/Richard/main/Favico.jpg" alt="Richard — a white particle ring on black" width="140">
</p>

<h1 align="center">Richard</h1>

<p align="center"><b>A self-hosted house companion that listens, acts — and keeps watch.</b><br>
Driven by a local LLM. No cloud, no accounts: every word of audio, every model, every
device call stays on your LAN.</p>

---

You talk to Richard by voice through the house or chat with it in a terminal. It controls
your smart home through Home Assistant, remembers things about you, and reasons on a
**self-hosted LLM** (llama.cpp, Ollama, or any OpenAI-compatible endpoint) running on your
own hardware.

But the part that makes Richard different is what happens when you *stop* talking to it.

## Automations you talk into existence

> *"Watch the workshop lamp and the kitchen temperature every 30 seconds. If the lamp is
> on while it's over 30 °C, turn the lamp off and tell me."*

That sentence — spoken or typed — becomes a **control loop**: a persistent background
monitor that survives restarts. No YAML, no automation editor, no trigger/condition/action
schema. The trigger lives as plain language, and when a monitored state changes, **the LLM
itself evaluates it with judgment**, can inspect or control your devices in response, and
files a notification in your event inbox only when the trigger actually applies. Routine
changes are recorded silently; nothing nags you.

How a loop runs:

- The first read establishes a **baseline** — creating a loop never fires it.
- On a state or attribute change, Richard wakes, judges your trigger against the change,
  acts if asked to, and reports only when it matters.
- Failures are honest: unreachable targets become visible loop health errors, and a missed
  evaluation becomes an inbox item saying so — never a silent skip.

Loops come in two kinds, and they compose:

- **Change loops** (the default) poll their targets — from every 5 seconds to once a day —
  and wake Richard only when state differs.
- **Scheduled loops** wake Richard at *times*: daily/weekly (`{"at": "08:00", "days":
  ["mon"]}`), recurring (`{"every_seconds": 3600}`), or one-shot (`{"once_at": "…"}`,
  auto-disabled after firing). With zero targets, a scheduled loop is simply a reminder.
- **Composition** is where it gets interesting: a change loop notices the front door
  opened, and Richard arms a one-shot to re-check in ten minutes — *"is it still open?"* —
  a duration condition, built conversationally from the two primitives.

A schedule missed while Richard was down fires once on startup rather than being skipped.
Loops and their notifications persist in `~/.richard/control-loops.db`, are visible and
editable in the web UI, and the monitor runs whenever Richard is running — `richard serve`
is the intended always-on mode.

## Verified, never assumed

Richard is structurally forbidden from bluffing about the physical world. Every
state-changing command is followed by a fresh read-back, and the result is graded:

- **CONFIRMED** — the device reports the requested state; only now may Richard say it worked.
- **MISMATCH** — the device answered with a *different* state; Richard must say so.
- **UNCONFIRMED** — the command was accepted but the result couldn't be read back.
- **FAILED** — the command was rejected or the target wasn't found.

These outcomes are authoritative over the LLM's own expectations, and the same machinery
backs diagnostic tools (`refresh`, `diagnose`, `verify`) the LLM uses proactively when you
ask *"why didn't the kitchen light turn on?"*

## The rest of the companion

- **Realtime voice** — an OpenAI-compatible `/v1/realtime` WebSocket subset: 16 kHz
  streaming audio in, Silero VAD endpointing, in-process faster-whisper STT, progressive
  text + PCM audio out, with true VAD barge-in — talk over Richard and it stops to listen,
  client-side tools and pictures (the Reachy Mini app's `camera`). Anything that speaks
  the protocol shape can point at your box.
- **Vision** — attach a picture in the web UI, or let Richard look through the webcam in
  voice mode; the same browser camera track can also feed ambient perception, with one
  bounded frame upload at a time. The brain sees requested pictures as OpenAI content
  parts. See [`docs/vision.md`](docs/vision.md).
- **Ambient perception** — with the `perception` plugin Richard watches the camera
  continuously, notices who arrives and leaves (recognition is opt-in and local), and is told
  only about transitions. See [`docs/perception.md`](docs/perception.md).
- **Multi-room satellites** — any mic + speaker device that speaks Richard's small
  WebSocket protocol becomes a voice satellite, playing replies sentence-by-sentence while
  Richard is still thinking.
- **Home Assistant** — entity discovery, live state, and service calls through the
  [REST API](https://developers.home-assistant.io/docs/api/rest/), all subject to the
  verification contract above.
- **Memory + personality** — Richard curates a persistent memory of you mid-conversation,
  deciding what's worth keeping (`remember`) and what to drop (`forget`). No black-box
  retrieval: every fact is plainly listed — and deletable — in the web UI or `richard
  memory`, and the whole memory rides along on every surface: terminal, voice, satellites,
  and inside control-loop evaluations, so the loop watching your thermostat knows you run
  cold. Personality comes with tunable dials (humour, honesty, directness).
- **Web UI** — a dependency-free control panel with a terminal aesthetic: conversation
  first, browser voice, loops and inbox, memory, and full configuration.
- **A portable CLI** — the lean core (three dependencies, no web framework) runs on any
  Unix box; everything heavy lives behind the optional `voice` extra.

**Plugins.** Connections (Home Assistant today, the Reachy body next) are plugins discovered through Python entry points: `richard plugins list|enable|disable|config|install`. A disabled plugin contributes no tools and no prompt text. See `docs/plugins.md`.

## Install & run

```bash
pipx install richard-companion   # the portable CLI (installs the `richard` command)
richard                    # interactive terminal chat
richard voice              # local push-to-talk voice
richard serve              # the always-on host: loops, satellites, realtime API, web UI
```

### Hardware: best with an NVIDIA GPU

The lean core and terminal chat run on any Unix box. But for the full experience — the
natural cloned voice and the most accurate speech recognition at conversational latency —
the always-on host wants a machine with an **NVIDIA GPU and the CUDA driver already
installed**:

- **NVIDIA GPU, ≥ 4 GB VRAM** — faster-whisper `large-v3-turbo` STT plus Chatterbox
  voice-cloned TTS. This is Richard at his best.
- **CPU-only (or < 4 GB VRAM)** — still fully functional: the installer falls back to
  faster-whisper `base.en` and Kokoro/Piper TTS. Fine for terminal chat and casual voice,
  but the voice is noticeably less natural and transcription less accurate.

The installer detects the hardware and picks the stack automatically, but it does **not**
install the NVIDIA driver — `nvidia-smi` must already work, or the box is treated as
CPU-only. (The LLM runs wherever your OpenAI-compatible endpoint lives and has its own
hardware appetite.)

### One-command brain box

On a Debian/Ubuntu machine, one command stands up a full brain box — Richard + STT + TTS,
LLM pointed at any OpenAI-compatible endpoint. It installs every OS dependency and a
standalone Python 3.11, then detects the hardware to pick the engines:

```bash
sudo ./install.sh --non-interactive \
  --llm-url http://<your-llm-host>:8080 --llm-model <model-name> --gpu 0
```

Full runbook: [`docs/install.md`](docs/install.md). An installed box updates itself:

```bash
sudo /opt/richard/update.sh --check   # show pending commits
sudo /opt/richard/update.sh           # fast-forward, verify, auto-rollback on failure
```

The updater preserves `~/.richard`, restarts only services that were active, verifies them,
and automatically rolls back a failed deployment.

## Voice

`richard serve` exposes the realtime subset at `/v1/realtime` (port `8766` by default). The
web UI streams microphone audio to Silero endpointing and faster-whisper, then receives
progressive text and PCM audio while supporting VAD barge-in. The wire contract is
documented in [`docs/realtime-api.md`](docs/realtime-api.md).

`richard voice` gives Richard local push-to-talk: press Enter, speak, and pause — Richard
transcribes locally, thinks, and replies aloud, streaming sentence-by-sentence so it starts
talking before the full reply is ready. Fully local, no cloud.

Requires **PortAudio** for local mic capture:

- macOS: `brew install portaudio`
- Raspberry Pi / Debian: `sudo apt install libportaudio2`

```bash
pip install 'richard-companion[voice]'   # or from a checkout: uv pip install -e ".[voice]"
richard voice
```

TTS is pluggable: Kokoro or Piper in-process on CPU, or a remote OpenAI-compatible
`/v1/audio/speech` server (Chatterbox with zero-shot voice cloning on GPU). Tune voice
settings through the web UI or `richard config`.

**Voice effect.** `[voice] tts_effect = "robot"` post-processes every spoken sentence (speaker colouring, ring modulation, bit crush) on any engine; `tts_effect_strength` (0–100) and `tts_effect_tone` (Hz) tune it from the web UI's Voice page. **Voice samples.** With the remote engine, the Voice page uploads a reference clip to the TTS server as a named voice and lists the server's voices.

## Web UI

`richard serve` hosts the dependency-free web interface on its own port (default `8771`).
The home screen is conversation-first: Richard appears as the white particle ring, with his
latest reply and compact text/push-to-talk controls. The menu opens automations (loops +
inbox), memory, system status, and a configuration index for the brain, personality, voice,
Home Assistant, satellites, and web access.

The panel talks to a small JSON API under `/api/*`; configuration changes persist to
`~/.richard/config.toml`.

```bash
richard config --set-web-port 9000      # change the port
richard config --set-web-host 127.0.0.1 # bind to localhost only
richard config --set-web-enabled off    # disable the panel on `serve`
```

## Home Assistant

Create a long-lived access token in your Home Assistant profile, then:

```bash
richard config \
  --set-ha-host homeassistant.local \
  --set-ha-port 8123 \
  --set-ha-token "$HA_TOKEN" \
  --set-ha-enabled on
```

The same settings live in the web panel, whose **Test connection** action reports
authentication/network errors and loads a searchable inventory of live entities. When
enabled, the LLM receives tools to list/filter entities, inspect state and attributes, and
call services — confined to the resolved entity's domain, with the state fetched again
after every call. The Home Assistant user behind the token determines the integration's
effective permissions, so a dedicated account is recommended.

For headless deployments use `RICHARD_HA_HOST`, `RICHARD_HA_PORT`, `RICHARD_HA_TOKEN`, and
`RICHARD_HA_ENABLED=on`; environment variables override the TOML file. Home Assistant is a
plugin: the settings live in `[plugins.home_assistant]`, and `richard plugins config
home_assistant host=... token=...` writes the same table (see `docs/plugins.md`).

## Development

```bash
uv venv && uv pip install -e ".[dev]"
uv run pytest
```

The core is dependency-light by design: `httpx`, `numpy`, `tomli-w`. Everything heavy
(STT, TTS, VAD, websockets) lives behind the optional `voice` extra.
