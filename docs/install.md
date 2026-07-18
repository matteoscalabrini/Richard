# Installing Richard — one command

Turn a Debian/Ubuntu box into a Richard brain box (Richard + STT + TTS, LLM pointed at any
OpenAI-compatible endpoint) with one command. The installer **detects the hardware and installs
the most capable stack that box can run** — no tiers, no menus.

## Requirements

- Debian/Ubuntu with `apt` (the installer refuses anything else) and root (`sudo`).
- **Best experience: an NVIDIA GPU (≥ 4 GB VRAM) with the CUDA driver already installed.**
  `nvidia-smi` must work *before* you run the installer — it detects the GPU but never
  installs the driver. Without a working driver the box is treated as CPU-only and gets
  the lighter voice stack (`base.en` STT, Kokoro/Piper TTS) instead of `large-v3-turbo`
  and the Chatterbox cloned voice.
- An OpenAI-compatible LLM endpoint reachable on your network (llama.cpp, Ollama, LM
  Studio, …).

## The one command

```bash
sudo ./install.sh
```

Running the checked-out script without overrides clones the official GitHub repository. To
install from a local checkout or another Git remote, supply `RICHARD_SRC`:

```bash
RICHARD_SRC=/path/to/Richard sudo -E ./install.sh    # interactive
```

## What it does

Two layers:

1. **`install.sh` (bash bootstrap, root).** Guarantees the things pip cannot provide, idempotently:
   - OS gate — refuses anything but Debian/Ubuntu with a clear message.
   - System packages via `apt`: `build-essential pkg-config git curl ca-certificates ffmpeg
     libsndfile1 portaudio19-dev espeak-ng python3-dev`.
   - A **standalone CPython 3.11 via uv** (the box's system Python is irrelevant).
   - Fetches the source, creates or reuses the venv, installs `richard[voice]`, exposes `richard`
     through `/usr/bin`, then runs `richard setup`.
   - Downloads and initializes Silero plus the selected faster-whisper model before starting the
     service, so the first conversation does not pay the model-download cost.
   - Installs `richard.service` for realtime voice, satellites, and web; the selected GPU is
     passed via `CUDA_VISIBLE_DEVICES`.
2. **`richard setup` (Python configurator).** `detect → plan → configure-llm → apply+save →
   provision (GPU only) → verify`. It picks the engines and deployment topology from the detected
   VRAM:
   - **No GPU / < 4 GB:** in-process Kokoro TTS + faster-whisper `base.en`; no sidecar services.
   - **≥ 4 GB:** in-process faster-whisper `large-v3-turbo` plus Chatterbox TTS as the only
     sidecar service (`chatterbox-tts.service`, `localhost:8004`).

## Unattended install

Thread the LLM config (and optional overrides) straight through the bootstrap to `richard setup`:

```bash
RICHARD_SRC=. sudo -E ./install.sh \
  --non-interactive \
  --llm-url http://192.168.1.50:8080 \
  --llm-model my-model \
  --gpu 0
```

Flags (all optional): `--llm-url`, `--llm-key` (cloud endpoints), `--llm-model`, `--gpu <id>`,
`--non-interactive`, and the hidden `--profile {cpu,gpu}` to force a topology for testing.

## Updating an installed box

`install.sh` installs [`update.sh`](../update.sh) at `/opt/richard/update.sh`. The update path is
separate from initial hardware detection and configuration: it never rewrites
`~/.richard/config.toml` or the memory/control-loop databases.

```bash
# Fetch and show pending commits without changing the deployment
sudo /opt/richard/update.sh --check

# Fast-forward, build the new environment, switch it in, and restart managed services
sudo /opt/richard/update.sh
```

The normal update flow:

1. Refuses a dirty checkout, concurrent update, detached HEAD, or non-fast-forward history.
2. Fetches the installed branch and creates rollback metadata under `/var/backups/richard/`.
3. Fast-forwards the source and builds a fresh Python 3.11 environment under
   `/opt/richard/.venvs/<commit>` before touching a running Richard service.
4. Stops any active `richard.service`/`richard-serve.service`, atomically points `.venv` at the
   new environment, then restores only the services that were active before the update.
5. Initializes the configured Silero and faster-whisper models before bringing Richard back.
6. If Richard-owned TTS files changed, it backs up and redeploys the Chatterbox reference voice
   and unit while preserving the selected GPU.
7. Verifies systemd state, Chatterbox's HTTP target, and a realtime WebSocket handshake that must
   emit `session.created`. Any failure resets Git, restores the prior environment and TTS state,
   and restarts the previous services.

Options:

- `--tts-deps` — explicitly synchronize/upgrade Chatterbox packages in `/opt/voice/venv`.
  Ordinary updates deliberately do not advance the external Chatterbox server checkout.
- `--skip-tts` — leave the Chatterbox assets, dependencies, unit, and process untouched.
- `--branch NAME` / `--remote NAME` — select the fetch source; the branch must already be checked
  out, preventing an accidental deployment-channel switch.

Paths and health retry timing can be overridden with the environment variables listed by
`update.sh --help`.

## Re-running the installer

The installer is rerunnable for repair/reconfiguration: present apt packages, uv, the main venv,
the GPU TTS venv, and the Chatterbox checkout are reused. It refreshes packages/assets, rewrites
the selected hardware plan, prepares realtime models, and restarts managed units. If an
interrupted clone left an incomplete Chatterbox directory, the installer preserves it as
`Chatterbox-TTS-Server.incomplete-<timestamp>` before cloning a clean copy. Use `update.sh` for
routine releases; re-run `install.sh` only when changing hardware topology or repairing an
incomplete installation.

## Out of scope (v1)

Native Anthropic LLM (use an OpenAI-compatible proxy); NVIDIA **driver** install (assumed
present); non-apt distros.
