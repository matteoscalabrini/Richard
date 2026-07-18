from richard.voice.segmenter import SentenceSegmenter


def test_emits_complete_sentence():
    assert SentenceSegmenter().feed("Hello there.") == ["Hello there."]


def test_buffers_until_ender():
    s = SentenceSegmenter()
    assert s.feed("Fan is ") == []
    assert s.feed("off now.") == ["Fan is off now."]


def test_multiple_sentences_in_one_delta():
    assert SentenceSegmenter().feed("Done. Anything else?") == ["Done.", "Anything else?"]


def test_decimals_are_not_split():
    assert SentenceSegmenter().feed("Set to 3.5 percent.") == ["Set to 3.5 percent."]


def test_newline_is_a_boundary():
    assert SentenceSegmenter().feed("Line one\nLine two.") == ["Line one", "Line two."]


def test_flush_returns_trailing_remainder():
    s = SentenceSegmenter()
    assert s.feed("No ender yet") == []
    assert s.flush() == "No ender yet"
    assert s.flush() == ""
