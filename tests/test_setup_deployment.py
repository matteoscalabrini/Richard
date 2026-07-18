import asyncio
import json

import pytest

from richard.config import Config


def test_prepare_realtime_models_uses_configured_stt_model():
    from richard.setup.deployment import prepare_realtime_models

    config = Config()
    config.voice.stt_model = "large-v3-turbo"
    calls = []

    class Model:
        def transcribe(self, audio, **kwargs):
            calls.append(("decode", audio.shape, str(audio.dtype), kwargs))
            return iter(()), object()

    def model_factory(model, **kwargs):
        calls.append((model, kwargs))
        return Model()

    prepare_realtime_models(
        config,
        write=lambda line: None,
        ensure_vad=lambda **kwargs: calls.append(("vad", kwargs)),
        model_factory=model_factory,
    )

    assert calls[0][0] == "vad"
    assert calls[1] == (
        "large-v3-turbo",
        {"device": "auto", "compute_type": "default"},
    )
    assert calls[2] == (
        "decode",
        (16000,),
        "float32",
        {
            "beam_size": 1,
            "vad_filter": False,
            "condition_on_previous_text": False,
        },
    )


def test_verify_realtime_accepts_session_created_and_sends_token():
    from richard.setup.deployment import verify_realtime
    import websockets

    async def scenario():
        paths = []

        async def handler(socket):
            paths.append(socket.request.path)
            await socket.send(json.dumps({"type": "session.created", "session": {}}))

        async with websockets.serve(handler, "127.0.0.1", 0) as server:
            config = Config()
            config.web.tls = False
            config.realtime.port = server.sockets[0].getsockname()[1]
            config.realtime.token = "a token/+"
            event = await verify_realtime(config, timeout=1.0)

        assert paths == ["/v1/realtime?token=a%20token%2F%2B"]
        assert event["type"] == "session.created"

    asyncio.run(scenario())


def test_verify_realtime_rejects_wrong_first_event():
    from richard.setup.deployment import verify_realtime
    import websockets

    async def scenario():
        async def handler(socket):
            await socket.send(json.dumps({"type": "response.created"}))

        async with websockets.serve(handler, "127.0.0.1", 0) as server:
            config = Config()
            config.web.tls = False
            config.realtime.port = server.sockets[0].getsockname()[1]
            with pytest.raises(RuntimeError, match="unexpected realtime event"):
                await verify_realtime(config, timeout=1.0)

    asyncio.run(scenario())


def test_verify_realtime_rejects_disabled_api():
    from richard.setup.deployment import verify_realtime

    config = Config()
    config.realtime.enabled = False

    with pytest.raises(RuntimeError, match="disabled"):
        asyncio.run(verify_realtime(config, timeout=0.1))
