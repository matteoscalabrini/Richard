from __future__ import annotations

import subprocess
from datetime import UTC, datetime
from pathlib import Path
from typing import Callable

from richard.setup.plan import InstallPlan
from richard.setup.units import render_chatterbox_unit

VOICE_ROOT = "/opt/voice"
VENV = f"{VOICE_ROOT}/venv"
CHATTERBOX_DIR = f"{VOICE_ROOT}/Chatterbox-TTS-Server"
UNIT_DIR = "/etc/systemd/system"


def _default_run(cmd: list[str], **kw) -> None:
    subprocess.run(cmd, check=True, **kw)


def _default_write_unit(name: str, text: str) -> None:
    Path(UNIT_DIR, name).write_text(text, encoding="utf-8")


def provision_voice_services(
    plan: InstallPlan,
    gpu: str,
    reference_clip: str,
    run: Callable[..., None] = _default_run,
    write_unit: Callable[[str, str], None] = _default_write_unit,
    exists: Callable[[str], bool] = lambda path: Path(path).exists(),
    notify: Callable[[str], None] = print,
) -> None:
    """Stand up the Chatterbox systemd service (GPU path only).
    Automates docs/voice-server-setup.md. No-op when the plan needs no services."""
    if not plan.needs_services:
        return

    # 1. venv + base apt-level deps are already present from the bootstrap. Keep the
    # existing environment on a setup re-run; recreating it can interrupt live services.
    if not exists(f"{VENV}/bin/python"):
        run(["python3", "-m", "venv", VENV])

    # 2. Chatterbox TTS server. A previous interrupted clone can leave a non-empty
    # destination without .git. Preserve that directory before retrying instead of
    # deleting it or asking Git to clone over it. Also accept a complete checkout
    # installed from an archive, where .git is intentionally absent.
    chatterbox_ready = exists(f"{CHATTERBOX_DIR}/.git") or (
        exists(f"{CHATTERBOX_DIR}/server.py")
        and exists(f"{CHATTERBOX_DIR}/requirements-nvidia.txt")
    )
    if not chatterbox_ready:
        if exists(CHATTERBOX_DIR):
            timestamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S%fZ")
            incomplete_dir = f"{CHATTERBOX_DIR}.incomplete-{timestamp}"
            notify(f"Preserving incomplete Chatterbox directory as {incomplete_dir}")
            run(["mv", CHATTERBOX_DIR, incomplete_dir])
        run(["git", "clone", "https://github.com/devnen/Chatterbox-TTS-Server.git", CHATTERBOX_DIR])
    pip = f"{VENV}/bin/pip"
    run([pip, "install", "-r", f"{CHATTERBOX_DIR}/requirements-nvidia.txt"])
    # The chatterbox engine package itself is not in requirements-nvidia.txt — install it
    # (--no-deps, pinned helpers) or the server dies with ModuleNotFoundError: 'chatterbox'.
    run([
        pip, "install", "--no-deps",
        "git+https://github.com/devnen/chatterbox-v2.git@master",
        "s3tokenizer==0.3.0", "onnx==1.16.0",
    ])
    run([pip, "install", "protobuf==3.20.3"])  # pin after onnx to fix the descript/protobuf clash
    run(["mkdir", "-p", f"{CHATTERBOX_DIR}/reference_audio"])
    run(["cp", reference_clip, f"{CHATTERBOX_DIR}/reference_audio/richard-voice.wav"])

    # 3. systemd unit.
    write_unit(
        "chatterbox-tts.service",
        render_chatterbox_unit(workdir=CHATTERBOX_DIR, python=f"{VENV}/bin/python", gpu=gpu),
    )
    run(["systemctl", "daemon-reload"])
    run(["systemctl", "enable", "chatterbox-tts.service"])
    # Restart rather than `enable --now`: on a setup re-run the units are already
    # active, and `--now` would not load the newly deployed shim/unit contents.
    run(["systemctl", "restart", "chatterbox-tts.service"])
