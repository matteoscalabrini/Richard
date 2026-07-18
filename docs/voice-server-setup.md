# Chatterbox TTS sidecar (GPU host)

Richard's realtime STT and VAD run inside `richard serve`: Silero handles continuous endpointing
and faster-whisper transcribes turns with `large-v3-turbo` on GPU installations. The only external
voice process is Chatterbox TTS on `localhost:8004`.

The managed all-in-one deployment runs Richard and Chatterbox in the same GPU-enabled Debian or
Ubuntu host. Both receive the selected device through `CUDA_VISIBLE_DEVICES`; choose a GPU with
enough room for Chatterbox and faster-whisper alongside any other inference workloads.

## Directory layout

```text
/opt/richard/                         Richard checkout and versioned environments
/opt/voice/venv/                     Chatterbox Python environment
/opt/voice/Chatterbox-TTS-Server/    External Chatterbox checkout
```

`install.sh` creates and maintains this layout through `richard setup`. The external Chatterbox
checkout is reused on subsequent setup runs and is never advanced implicitly by `update.sh`.

## Chatterbox (`:8004`)

The setup provisioner performs the equivalent of:

```bash
python3 -m venv /opt/voice/venv
git clone https://github.com/devnen/Chatterbox-TTS-Server.git \
  /opt/voice/Chatterbox-TTS-Server
/opt/voice/venv/bin/pip install \
  -r /opt/voice/Chatterbox-TTS-Server/requirements-nvidia.txt
/opt/voice/venv/bin/pip install --no-deps \
  git+https://github.com/devnen/chatterbox-v2.git@master \
  s3tokenizer==0.3.0 onnx==1.16.0
/opt/voice/venv/bin/pip install protobuf==3.20.3
```

It copies `assets/voice/richard-voice.wav` into `reference_audio/` and renders
`chatterbox-tts.service` with:

```ini
[Service]
WorkingDirectory=/opt/voice/Chatterbox-TTS-Server
Environment=CUDA_VISIBLE_DEVICES=<gpu>
ExecStart=/opt/voice/venv/bin/python server.py
Restart=on-failure
```

Richard uses the configured remote TTS adapter at `http://127.0.0.1:8004` while keeping STT local
to its own process.

## Realtime models

Managed install and update run:

```bash
/opt/richard/.venv/bin/python -m richard.setup.deployment prepare
```

This downloads and initializes Silero plus the configured faster-whisper model before Richard is
declared healthy. Models use the normal cache under the account running `richard.service`.

`richard.service` hosts the web UI, relay, and `/v1/realtime` endpoint. Its health gate connects to
the configured loopback WebSocket and requires the first event to be `session.created`. TLS and
the configured realtime token are handled by the probe.

## Updates

Routine updates rebuild Richard's `richard[voice]` environment, prepare its realtime models,
atomically switch `.venv`, and restart only services that were active. Chatterbox is redeployed
only when Richard-owned TTS assets or unit/provisioning code changed.

Use `--tts-deps` to refresh Chatterbox's Python packages deliberately. Use `--skip-tts` to leave
its assets, dependencies, unit, and process untouched. A failed health check restores the previous
Richard environment and Chatterbox state.
