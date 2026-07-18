"""Progressive chunking of streamed LLM text for TTS.

The first chunk may break at any clause boundary once min_first_words words have
arrived — the listener hears Richard begin almost immediately. Every later chunk
breaks only at sentence enders and only once the accumulated candidate reaches
min_chunk_chars (complete sentences below the threshold wait for more text or
flush()), so the synthesizer gets sentence-or-paragraph-sized chunks and the
prosody stays natural. A newline always breaks. Known, accepted limitation
(inherited from SentenceSegmenter): abbreviations like "Dr." split early.
"""
from __future__ import annotations

_SENTENCE_ENDERS = ".?!\n"
_CLAUSE_ENDERS = ".?!\n,;:"


class ProgressiveChunker:
    def __init__(self, *, min_first_words: int = 2, min_chunk_chars: int = 60) -> None:
        self._buf = ""
        self._first_emitted = False
        self._min_first_words = min_first_words
        self._min_chunk_chars = min_chunk_chars

    def feed(self, delta: str) -> list[str]:
        self._buf += delta
        out: list[str] = []
        while (chunk := self._next_chunk()) is not None:
            out.append(chunk)
        return out

    def _next_chunk(self) -> str | None:
        enders = _SENTENCE_ENDERS if self._first_emitted else _CLAUSE_ENDERS
        for i, ch in enumerate(self._buf):
            if ch not in enders or self._is_decimal_point(i):
                continue
            candidate = self._buf[: i + 1].strip()
            if len(candidate.replace(" ", "")) < 2:
                continue
            if not self._first_emitted:
                # A clause break needs min_first_words behind it; a sentence
                # ender always emits (short answers like "Yes." must not stall).
                if ch not in _SENTENCE_ENDERS and len(candidate.split()) < self._min_first_words:
                    continue
            elif ch != "\n" and len(candidate) < self._min_chunk_chars:
                continue
            self._buf = self._buf[i + 1 :]
            self._first_emitted = True
            return candidate
        return None

    def _is_decimal_point(self, i: int) -> bool:
        # A dot between digits is decimal; a dot at the end of the buffer after a
        # digit MIGHT be ("3." awaiting "5") — hold it until the next delta or flush.
        if self._buf[i] != "." or i == 0 or not self._buf[i - 1].isdigit():
            return False
        return i == len(self._buf) - 1 or self._buf[i + 1].isdigit()

    def flush(self) -> str:
        rest = self._buf.strip()
        self._buf = ""
        return rest
