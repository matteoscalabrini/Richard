"""Post-processing effects on synthesized speech (spec 2026-09-09).

An effect runs on int16 mono PCM after any TTS engine and before any transport, so
every output path (realtime, web, satellites, local loop) sounds the same. State (the
FIR tail and the ring-modulator sample counter) persists across calls: a sentence
processed in chunks equals the same sentence processed whole.
"""
from __future__ import annotations

import numpy as np

EFFECTS = ("none", "robot")


def _bandpass_kernel(samplerate: int, low_hz: float, high_hz: float, taps: int) -> np.ndarray:
    """Windowed-sinc band-pass (Hamming), unity gain at the band's geometric centre."""
    n = np.arange(taps) - (taps - 1) / 2.0

    def lowpass(cutoff_hz: float) -> np.ndarray:
        fc = cutoff_hz / samplerate
        return 2.0 * fc * np.sinc(2.0 * fc * n)

    kernel = (lowpass(high_hz) - lowpass(low_hz)) * np.hamming(taps)
    centre = 2.0 * np.pi * np.sqrt(low_hz * high_hz) / samplerate
    gain = abs(np.sum(kernel * np.exp(-1j * centre * np.arange(taps))))
    return (kernel / gain).astype(np.float32)


class RobotEffect:
    """Speaker colouring + ring modulation + bit crush, with a fixed makeup gain."""

    def __init__(
        self,
        strength: int = 50,
        tone_hz: float = 40.0,
        *,
        taps: int = 101,
        low_hz: float = 250.0,
        high_hz: float = 5000.0,
    ) -> None:
        self.strength = max(0, min(100, int(strength)))
        self.tone_hz = max(1.0, float(tone_hz))
        self._taps = taps
        self._low = low_hz
        self._high = high_hz
        self._samplerate: int | None = None
        self._kernel: np.ndarray | None = None
        self._tail = np.zeros(taps - 1, dtype=np.float32)
        self._count = 0  # samples seen since reset; drives the modulator phase exactly

    def reset(self) -> None:
        self._tail = np.zeros(self._taps - 1, dtype=np.float32)
        self._count = 0

    def _prepare(self, samplerate: int) -> None:
        if samplerate != self._samplerate:
            self._samplerate = samplerate
            self._kernel = _bandpass_kernel(samplerate, self._low, self._high, self._taps)
            self.reset()

    def process(self, pcm: bytes, samplerate: int) -> bytes:
        if self.strength == 0:
            return pcm
        usable = len(pcm) - len(pcm) % 2
        if usable == 0:
            return b""
        self._prepare(samplerate)
        s = self.strength / 100.0
        x = np.frombuffer(pcm[:usable], dtype="<i2").astype(np.float32)

        # 1. Speaker colouring: FIR band-pass with the previous block's tail prepended,
        #    so the filter never sees a block boundary.
        padded = np.concatenate([self._tail, x])
        filtered = np.convolve(padded, self._kernel, mode="valid").astype(np.float32)
        self._tail = padded[-(self._taps - 1):].copy()
        y = x + s * (filtered - x)

        # 2. Ring modulation. The carrier argument is omega * absolute sample index, so a
        #    chunk boundary produces the same float64 arguments as one long buffer.
        omega = 2.0 * np.pi * self.tone_hz / samplerate
        index = self._count + np.arange(x.size, dtype=np.int64)
        carrier = np.sin(omega * index.astype(np.float64)).astype(np.float32)
        self._count += int(x.size)
        mix = 0.6 * s
        y = y * (1.0 - mix) + y * carrier * mix

        # 3. Bit-depth reduction: 16 bits at s=0 down to 8 bits at s=1.
        step = 2.0 ** (8.0 * s)
        y = np.round(y / step) * step

        # 4. Fixed makeup gain for the ring-modulation loss (a sine's RMS is 1/sqrt 2).
        #    Computed from the settings, never from the block, so chunks stay seamless.
        y = y / (1.0 - mix + mix / np.sqrt(2.0))
        return np.clip(np.round(y), -32768, 32767).astype("<i2").tobytes()


def effect_from_config(name: str, strength: int, tone_hz: float) -> RobotEffect | None:
    """The effect for the [voice] settings; None for "none" and for unknown names."""
    if name == "robot":
        return RobotEffect(strength=strength, tone_hz=tone_hz)
    return None


class EffectTTS:
    """Wraps any engine exposing `synth(text) -> bytes` and `samplerate`; applies the effect."""

    def __init__(self, inner, effect: RobotEffect) -> None:
        self._inner = inner
        self._effect = effect

    @property
    def samplerate(self) -> int:
        return self._inner.samplerate

    def synth(self, text: str) -> bytes:
        return self._effect.process(self._inner.synth(text), self._inner.samplerate)
