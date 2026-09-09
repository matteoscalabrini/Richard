import json

import httpx

from richard.voice.voices import VoiceLibrary


def _client(handler):
    return httpx.Client(transport=httpx.MockTransport(handler))


def test_list_parses_names_and_uploaded_metadata():
    def handler(request):
        assert request.method == "GET" and str(request.url).endswith("/v1/audio/voices")
        return httpx.Response(200, json={
            "voices": ["clap1", "default"],
            "uploaded_voices": [{"name": "clap1", "ref_text": "oh hi", "created_at": 1788950000, "file_size": 1}],
        })

    library = VoiceLibrary("http://host:8091/", client=_client(handler))
    assert library.list() == {"voices": ["clap1", "default"], "uploaded": [{"name": "clap1", "ref_text": "oh hi", "created_at": 1788950000}]}


def test_upload_sends_multipart_fields():
    captured = {}

    def handler(request):
        captured["content_type"] = request.headers["content-type"]
        captured["body"] = request.content
        return httpx.Response(200, json={"name": "clap1v"})

    library = VoiceLibrary("http://host:8091", client=_client(handler))
    reply = library.upload("clap1v", b"RIFF....", "clap1v.wav", transcript="oh hi", consent="web-clap1v-2026-09-09")
    assert reply == {"name": "clap1v"}
    assert captured["content_type"].startswith("multipart/form-data")
    body = captured["body"]
    for needle in (b'name="audio_sample"; filename="clap1v.wav"', b"audio/wav", b'name="name"', b"clap1v", b'name="ref_text"', b"oh hi", b'name="consent"', b"web-clap1v-2026-09-09", b"RIFF...."):
        assert needle in body


def test_errors_propagate_as_httpx_errors():
    library = VoiceLibrary("http://host:8091", client=_client(lambda request: httpx.Response(500, text="boom")))
    try:
        library.list()
    except httpx.HTTPError:
        pass
    else:
        raise AssertionError("expected an httpx error")
