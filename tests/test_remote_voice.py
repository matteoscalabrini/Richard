import io
import json
import wave

import httpx

from richard.voice.remote import RemoteSTT, RemoteTTS


def _client(handler):
    return httpx.Client(transport=httpx.MockTransport(handler))


def _wav(pcm: bytes, samplerate: int = 24000) -> bytes:
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(samplerate)
        w.writeframes(pcm)
    return buf.getvalue()


def test_remote_tts_posts_and_returns_pcm():
    captured = {}
    pcm = b"\x01\x02" * 100

    def handler(request):
        captured["url"] = str(request.url)
        captured["json"] = json.loads(request.content)
        return httpx.Response(200, content=_wav(pcm, 24000))

    tts = RemoteTTS("http://host:8004/", voice="tars", client=_client(handler))
    out = tts.synth("Fan's off.")
    assert captured["url"].endswith("/v1/audio/speech")
    assert captured["json"]["input"] == "Fan's off."
    assert captured["json"]["voice"] == "tars"
    assert tts.samplerate == 24000
    assert out == pcm


def test_remote_stt_posts_audio_and_returns_text():
    captured = {}

    def handler(request):
        captured["url"] = str(request.url)
        captured["had_body"] = bool(request.content)
        return httpx.Response(200, json={"text": "  turn the fan off  "})

    stt = RemoteSTT("http://host:8005", client=_client(handler))
    text = stt.transcribe(b"\x00\x00" * 320, 16000)
    assert captured["url"].endswith("/v1/audio/transcriptions")
    assert captured["had_body"] is True
    assert text == "turn the fan off"


def test_remote_stt_blank_pcm_returns_empty_without_request():
    def handler(request):
        raise AssertionError("should not POST on blank audio")

    assert RemoteSTT("http://host", client=_client(handler)).transcribe(b"", 16000) == ""


def test_remote_tts_sends_tuning_params():
    captured = {}

    def handler(request):
        captured["json"] = json.loads(request.content)
        return httpx.Response(200, content=_wav(b"\x00\x00" * 10, 24000))

    tts = RemoteTTS(
        "http://host:8004", voice="tars", client=_client(handler),
        exaggeration=0.7, cfg_weight=0.3, temperature=1.1, speed_factor=1.2,
    )
    tts.synth("hi")
    j = captured["json"]
    assert j["exaggeration"] == 0.7
    assert j["cfg_weight"] == 0.3
    assert j["temperature"] == 1.1
    assert j["speed_factor"] == 1.2


def test_remote_stt_transcribe_file_posts_multipart():
    captured = {}

    def handler(request):
        captured["url"] = str(request.url)
        captured["has_body"] = bool(request.content)
        return httpx.Response(200, json={"text": "  hello there "})

    stt = RemoteSTT("http://host:8005", client=_client(handler))
    text = stt.transcribe_file(b"webm-bytes", filename="audio.webm")
    assert captured["url"].endswith("/v1/audio/transcriptions")
    assert captured["has_body"] is True
    assert text == "hello there"
