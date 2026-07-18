from richard.voice.audio import play, record_stream


def test_record_stream_yields_chunks_from_factory():
    chunks = [b"\x00" * 640, b"\x11" * 640]

    class FakeStream:
        def __init__(self):
            self.i = 0
            self.closed = False

        def start(self):
            pass

        def read(self, n):
            c = chunks[self.i]
            self.i += 1
            return (c, False)

        def stop(self):
            pass

        def close(self):
            self.closed = True

    stream = FakeStream()
    gen = record_stream(_stream_factory=lambda: stream)
    assert [next(gen), next(gen)] == chunks
    gen.close()  # closing the generator must run the finally and release the mic
    assert stream.closed is True


def test_play_writes_pcm_to_factory_stream():
    written = {}

    class FakeStream:
        def start(self):
            pass

        def write(self, pcm):
            written["pcm"] = pcm

        def stop(self):
            pass

        def close(self):
            pass

    play(b"abc", 16000, _stream_factory=lambda: FakeStream())
    assert written["pcm"] == b"abc"
