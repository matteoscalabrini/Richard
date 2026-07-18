from __future__ import annotations

_CHATTERBOX = """\
[Unit]
Description=Richard Chatterbox TTS
After=network.target

[Service]
Type=simple
WorkingDirectory={workdir}
Environment=CUDA_VISIBLE_DEVICES={gpu}
ExecStart={python} server.py
Restart=on-failure

[Install]
WantedBy=multi-user.target
"""

def render_chatterbox_unit(workdir: str, python: str, gpu: str) -> str:
    return _CHATTERBOX.format(workdir=workdir, python=python, gpu=gpu)
