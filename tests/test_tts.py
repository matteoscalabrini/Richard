import pytest

from richard.voice.tts import SpeechPipeline, ensure_voice


def test_ensure_voice_downloads_both_files(tmp_path):
    class Resp:
        def __init__(self, content):
            self.content = content

        def raise_for_status(self):
            pass

    class Client:
        def __init__(self):
            self.urls = []

        def get(self, url):
            self.urls.append(url)
            return Resp(b"x" * 4096)

    client = Client()
    msgs = []
    onnx = ensure_voice("TARS", voices_dir=tmp_path, client=client, write=msgs.append)
    assert onnx == tmp_path / "TARS.onnx"
    assert (tmp_path / "TARS.onnx").read_bytes() == b"x" * 4096
    assert (tmp_path / "TARS.onnx.json").exists()
    assert len(client.urls) == 2
    assert any("Downloading" in m for m in msgs)


def test_ensure_voice_skips_when_present(tmp_path):
    (tmp_path / "TARS.onnx").write_bytes(b"x" * 4096)
    (tmp_path / "TARS.onnx.json").write_bytes(b"x" * 4096)

    class Boom:
        def get(self, url):
            raise AssertionError("should not download")

    assert ensure_voice("TARS", voices_dir=tmp_path, client=Boom()) == tmp_path / "TARS.onnx"


def test_ensure_voice_raises_on_download_failure(tmp_path):
    class FailClient:
        def get(self, url):
            class R:
                def raise_for_status(self):
                    raise RuntimeError("404")

            return R()

    with pytest.raises(RuntimeError):
        ensure_voice("TARS", voices_dir=tmp_path, client=FailClient())


class FakeTTS:
    samplerate = 16000

    def synth(self, text):
        return text.encode()


def test_speech_pipeline_plays_sentences_in_order():
    played = []
    sp = SpeechPipeline(FakeTTS(), lambda pcm, rate: played.append(pcm.decode()))
    sp.say("one")
    sp.say("two")
    sp.say("three")
    sp.drain()
    sp.close()
    assert played == ["one", "two", "three"]


def test_speech_pipeline_stop_drops_pending():
    played = []
    sp = SpeechPipeline(FakeTTS(), lambda pcm, rate: played.append(pcm.decode()))
    sp.stop()
    sp.say("ignored")
    sp.drain()
    sp.close()
    assert played == []


def test_piper_tts_synth_joins_audio_chunks():
    from richard.voice.tts import PiperTTS

    class FakeChunk:
        def __init__(self, audio):
            self.audio_int16_bytes = audio

    class FakeConfig:
        sample_rate = 22050

    class FakeVoice:
        config = FakeConfig()

        def synthesize(self, text):
            yield FakeChunk(b"\x01\x02")
            yield FakeChunk(b"\x03\x04")

    tts = PiperTTS("ignored.onnx", _voice=FakeVoice())
    assert tts.synth("hi") == b"\x01\x02\x03\x04"
    assert tts.samplerate == 22050


def test_speech_pipeline_clear_keeps_pipeline_alive():
    played = []
    sp = SpeechPipeline(FakeTTS(), lambda pcm, rate: played.append(pcm.decode()))
    sp.clear()  # unlike stop(), clear() must not latch the pipeline off
    sp.say("after")
    sp.drain()
    sp.close()
    assert played == ["after"]


def test_ensure_kokoro_downloads_model_and_voices(tmp_path):
    from richard.voice.tts import KOKORO_MODEL_FILE, KOKORO_VOICES_FILE, ensure_kokoro

    class Resp:
        def __init__(self, content):
            self.content = content

        def raise_for_status(self):
            pass

    class Client:
        def __init__(self):
            self.urls = []

        def get(self, url):
            self.urls.append(url)
            return Resp(b"x" * 4096)

    client = Client()
    model, voices = ensure_kokoro(voices_dir=tmp_path, client=client, write=lambda s: None)
    assert model == tmp_path / KOKORO_MODEL_FILE
    assert voices == tmp_path / KOKORO_VOICES_FILE
    assert (tmp_path / KOKORO_MODEL_FILE).exists()
    assert (tmp_path / KOKORO_VOICES_FILE).exists()
    assert len(client.urls) == 2


def test_ensure_kokoro_skips_when_present(tmp_path):
    from richard.voice.tts import KOKORO_MODEL_FILE, KOKORO_VOICES_FILE, ensure_kokoro

    (tmp_path / KOKORO_MODEL_FILE).write_bytes(b"x" * 4096)
    (tmp_path / KOKORO_VOICES_FILE).write_bytes(b"x" * 4096)

    class Boom:
        def get(self, url):
            raise AssertionError("should not download")

    model, _ = ensure_kokoro(voices_dir=tmp_path, client=Boom())
    assert model == tmp_path / KOKORO_MODEL_FILE


def test_kokoro_synth_converts_float_to_int16_pcm():
    import struct

    np = pytest.importorskip("numpy")
    from richard.voice.tts import KokoroTTS

    class FakeKokoro:
        def __init__(self):
            self.kwargs = None

        def create(self, text, voice, speed, lang):
            self.kwargs = {"voice": voice, "speed": speed, "lang": lang}
            return (np.array([0.0, 1.0, -1.0, 0.5], dtype="float32"), 24000)

    fake = FakeKokoro()
    tts = KokoroTTS("m", "v", voice="bm_lewis", _kokoro=fake)
    pcm = tts.synth("hello")
    assert tts.samplerate == 24000
    assert len(pcm) == 8  # 4 samples * 2 bytes each
    vals = struct.unpack("<4h", pcm)
    assert vals[1] == 32767 and vals[2] == -32767  # clipped to int16 extremes
    assert fake.kwargs["voice"] == "bm_lewis"


def test_speech_pipeline_survives_synth_error(capsys):
    played = []

    class FlakyTTS:
        samplerate = 16000

        def synth(self, text):
            if text == "boom":
                raise RuntimeError("synth failed")
            return text.encode()

    sp = SpeechPipeline(FlakyTTS(), lambda pcm, rate: played.append(pcm.decode()))
    sp.say("boom")
    sp.say("ok")
    sp.drain()
    sp.close()
    assert played == ["ok"]  # the bad sentence is skipped, the next still plays
    assert "couldn't speak" in capsys.readouterr().err
