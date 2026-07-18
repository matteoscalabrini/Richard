from __future__ import annotations

# Known, accepted limitations for the TTS use case: abbreviations ("Mr.", "Dr.")
# split here, and a decimal split across two deltas ("3." then "5") emits "3."
# early. Both are rare in LLM output and harmless when spoken.
_ENDERS = ".?!\n"


class SentenceSegmenter:
    """Accumulate streamed text deltas, emitting complete sentences as boundaries appear."""

    def __init__(self, min_chars: int = 2) -> None:
        self._buf = ""
        self._min_chars = min_chars

    def feed(self, delta: str) -> list[str]:
        self._buf += delta
        out: list[str] = []
        start = 0
        for i, ch in enumerate(self._buf):
            if ch in _ENDERS and not self._is_decimal_point(i):
                candidate = self._buf[start : i + 1].strip()
                if len(candidate.replace(" ", "")) >= self._min_chars:
                    out.append(candidate)
                    start = i + 1
        self._buf = self._buf[start:]
        return out

    def _is_decimal_point(self, i: int) -> bool:
        return (
            self._buf[i] == "."
            and 0 < i < len(self._buf) - 1
            and self._buf[i - 1].isdigit()
            and self._buf[i + 1].isdigit()
        )

    def flush(self) -> str:
        rest = self._buf.strip()
        self._buf = ""
        return rest
