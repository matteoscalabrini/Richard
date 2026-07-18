"""Prepare and verify the in-process realtime voice deployment."""
from __future__ import annotations

import argparse
import asyncio
import json
import ssl
from collections.abc import Callable
from urllib.parse import quote

from richard.config import Config, load_config


def prepare_realtime_models(
    config: Config | None = None,
    *,
    write: Callable[[str], None] = print,
    ensure_vad=None,
    model_factory=None,
) -> None:
    """Download and initialize the configured VAD and STT models."""
    config = config or load_config()
    if ensure_vad is None:
        from richard.realtime.vad import ensure_silero

        ensure_vad = ensure_silero
    if model_factory is None:
        from faster_whisper import WhisperModel

        model_factory = WhisperModel

    ensure_vad(write=write)
    write(f"Preparing faster-whisper model {config.voice.stt_model}...")
    model = model_factory(
        config.voice.stt_model, device="auto", compute_type="default"
    )

    # Model construction downloads files but CTranslate2 loads CUDA libraries
    # only on the first encode. Decode silence here so installation fails early
    # when the GPU runtime cannot actually execute the model.
    import numpy as np

    segments, _info = model.transcribe(
        np.zeros(16000, dtype=np.float32),
        beam_size=1,
        vad_filter=False,
        condition_on_previous_text=False,
    )
    list(segments)


def _loopback_ssl_context() -> ssl.SSLContext:
    context = ssl.create_default_context()
    context.check_hostname = False
    context.verify_mode = ssl.CERT_NONE
    return context


async def verify_realtime(
    config: Config | None = None, *, timeout: float = 10.0
) -> dict:
    """Connect to the loopback realtime endpoint and require session.created."""
    config = config or load_config()
    if not config.realtime.enabled:
        raise RuntimeError("realtime API is disabled")

    import websockets

    token = (
        f"?token={quote(config.realtime.token, safe='')}"
        if config.realtime.token
        else ""
    )
    schemes = ("wss", "ws") if config.web.tls else ("ws",)
    errors = []
    for scheme in schemes:
        context = _loopback_ssl_context() if scheme == "wss" else None
        uri = (
            f"{scheme}://127.0.0.1:{config.realtime.port}"
            f"/v1/realtime{token}"
        )
        try:
            async with websockets.connect(
                uri, ssl=context, open_timeout=timeout
            ) as socket:
                event = json.loads(await asyncio.wait_for(socket.recv(), timeout))
            if event.get("type") != "session.created":
                raise RuntimeError(
                    f"unexpected realtime event: {event.get('type')!r}"
                )
            return event
        except Exception as exc:
            errors.append(f"{scheme}: {exc}")
    raise RuntimeError("realtime health check failed (" + "; ".join(errors) + ")")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("prepare", "verify"))
    args = parser.parse_args(argv)
    if args.action == "prepare":
        prepare_realtime_models()
    else:
        event = asyncio.run(verify_realtime())
        print(f"Realtime healthy ({event['type']}).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
