from richard.realtime.chunker import ProgressiveChunker


def test_first_chunk_breaks_at_clause_boundary():
    c = ProgressiveChunker(min_first_words=2)
    out = c.feed("Right then, let me check the living room for you.")
    assert out[0] == "Right then,"


def test_first_chunk_waits_for_min_words_before_comma():
    c = ProgressiveChunker(min_first_words=2)
    out = c.feed("Well, that depends, actually.")
    assert out[0] == "Well, that depends,"  # "Well," (1 word) is held


def test_short_full_sentence_emits_immediately():
    c = ProgressiveChunker()
    assert c.feed("Yes.") == ["Yes."]


def test_later_chunks_accumulate_to_min_chars():
    c = ProgressiveChunker(min_first_words=1, min_chunk_chars=40)
    assert c.feed("Sure thing,") == ["Sure thing,"]
    out = c.feed(" the fan is on. I set it to two. It will run for ten minutes total.")
    assert out == ["the fan is on. I set it to two. It will run for ten minutes total."]


def test_later_sentences_below_threshold_wait_for_flush():
    c = ProgressiveChunker(min_first_words=1, min_chunk_chars=40)
    c.feed("Sure,")
    assert c.feed(" Ok. Done.") == []
    assert c.flush() == "Ok. Done."


def test_first_chunk_word_gate_counts_candidate_across_deltas():
    c = ProgressiveChunker(min_first_words=4)
    out = []
    for t in ["Hi,", " this", " is", " a", " great", " day."]:
        out += c.feed(t)
    assert out == ["Hi, this is a great day."]  # "Hi," (1 word) never emits alone


def test_newline_always_emits():
    c = ProgressiveChunker(min_first_words=1, min_chunk_chars=60)
    c.feed("Hi,")
    assert c.feed(" line one\n") == ["line one"]


def test_decimal_points_do_not_split():
    c = ProgressiveChunker(min_first_words=1, min_chunk_chars=1)
    assert c.feed("It is 3.5 degrees. ") == ["It is 3.5 degrees."]


def test_flush_returns_tail_and_resets():
    c = ProgressiveChunker()
    c.feed("Leftover text with no boundary")
    assert c.flush() == "Leftover text with no boundary"
    assert c.flush() == ""


def test_decimal_split_across_deltas_does_not_break():
    c = ProgressiveChunker(min_first_words=1, min_chunk_chars=1)
    assert c.feed("It is 3.") == []  # trailing dot after digit: held
    assert c.feed("5 degrees. ") == ["It is 3.5 degrees."]


def test_digit_final_sentence_emits_at_flush():
    c = ProgressiveChunker(min_first_words=1, min_chunk_chars=1)
    assert c.feed("Set to 2.") == []
    assert c.flush() == "Set to 2."
