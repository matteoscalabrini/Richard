from richard.voice.stt import WhisperSTT

PCM = b"\x00\x00" * 320


class _Seg:
    def __init__(self, text):
        self.text = text


class FakeModel:
    def __init__(self, segments):
        self.segments = segments
        self.received = None
        self.kwargs = None

    def transcribe(self, audio, **kwargs):
        self.received = audio
        self.kwargs = kwargs
        return (iter(self.segments), {})


def test_joins_segment_text():
    stt = WhisperSTT(_model=FakeModel([_Seg("Turn the "), _Seg("fan off")]))
    assert stt.transcribe(PCM) == "Turn the fan off"


def test_blank_pcm_returns_empty_without_calling_model():
    model = FakeModel([_Seg("should not be used")])
    stt = WhisperSTT(_model=model)
    assert stt.transcribe(b"") == ""
    assert model.received is None


def test_whitespace_transcript_returns_empty():
    stt = WhisperSTT(_model=FakeModel([_Seg("   ")]))
    assert stt.transcribe(PCM) == ""


def test_enables_vad_filter_and_no_prev_text_conditioning():
    model = FakeModel([_Seg("hello")])
    WhisperSTT(_model=model).transcribe(PCM)
    assert model.kwargs["vad_filter"] is True
    assert model.kwargs["condition_on_previous_text"] is False


def test_vad_filter_can_be_disabled():
    model = FakeModel([_Seg("hello")])
    WhisperSTT(_model=model, vad_filter=False).transcribe(PCM)
    assert model.kwargs["vad_filter"] is False
