import numpy as np
from richard.satellite.audio_resample import resample_pcm16


def test_downsample_24k_to_16k_length():
    # 0.5 s of 24 kHz -> ~0.5 s of 16 kHz (2/3 the samples).
    src = (np.zeros(12000, dtype=np.int16)).tobytes()
    out = resample_pcm16(src, 24000, 16000)
    n = len(out) // 2
    assert abs(n - 8000) <= 2


def test_passthrough_when_rates_equal():
    src = np.array([1, -2, 3, -4], dtype=np.int16).tobytes()
    assert resample_pcm16(src, 16000, 16000) == src


def test_preserves_a_tone_roughly():
    t = np.arange(2400) / 24000.0
    tone = (np.sin(2 * np.pi * 440 * t) * 10000).astype(np.int16).tobytes()
    out = resample_pcm16(tone, 24000, 16000)
    arr = np.frombuffer(out, dtype=np.int16)
    assert len(arr) == 1600
    assert arr.max() > 5000  # tone survived
