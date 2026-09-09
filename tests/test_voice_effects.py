import numpy as np
import pytest

from richard.voice.effects import EFFECTS, RobotEffect, effect_from_config


def _tone_mix(samplerate=24000, seconds=0.5, amplitude=8000.0):
    t = np.arange(int(samplerate * seconds)) / samplerate
    x = amplitude * (np.sin(2 * np.pi * 400 * t) + 0.5 * np.sin(2 * np.pi * 1000 * t) + 0.25 * np.sin(2 * np.pi * 3000 * t))
    return np.round(x).astype("<i2").tobytes()


def _rms(pcm: bytes) -> float:
    x = np.frombuffer(pcm, dtype="<i2").astype(np.float64)
    return float(np.sqrt(np.mean(x * x))) if x.size else 0.0


def test_effects_list():
    assert EFFECTS == ("none", "robot")


def test_strength_zero_is_bit_exact_identity():
    pcm = _tone_mix()
    assert RobotEffect(strength=0).process(pcm, 24000) == pcm


def test_length_is_preserved_and_odd_trailing_byte_dropped():
    pcm = _tone_mix()
    out = RobotEffect(strength=50).process(pcm, 24000)
    assert len(out) == len(pcm)
    assert len(RobotEffect(strength=50).process(pcm + b"\x01", 24000)) == len(pcm)


def test_silence_stays_silence():
    silence = bytes(2 * 2400)
    assert RobotEffect(strength=100).process(silence, 24000) == silence


def test_two_halves_equal_the_whole():
    pcm = _tone_mix()
    whole = RobotEffect(strength=70, tone_hz=40.0).process(pcm, 24000)
    split = RobotEffect(strength=70, tone_hz=40.0)
    half = len(pcm) // 2
    half -= half % 2
    joined = split.process(pcm[:half], 24000) + split.process(pcm[half:], 24000)
    a = np.frombuffer(whole, dtype="<i2").astype(np.int32)
    b = np.frombuffer(joined, dtype="<i2").astype(np.int32)
    assert int(np.max(np.abs(a - b))) <= 1


def test_loudness_stays_within_3_db_of_input():
    pcm = _tone_mix()
    for strength in (30, 50, 100):
        out = RobotEffect(strength=strength).process(pcm, 24000)
        ratio = _rms(out) / _rms(pcm)
        assert 0.6 <= ratio <= 1.4, (strength, ratio)


def test_output_never_leaves_int16_range():
    pcm = _tone_mix(amplitude=32000.0)
    out = np.frombuffer(RobotEffect(strength=100).process(pcm, 24000), dtype="<i2")
    assert out.min() >= -32768 and out.max() <= 32767


def test_effect_actually_changes_the_signal():
    pcm = _tone_mix()
    out = RobotEffect(strength=50).process(pcm, 24000)
    assert out != pcm


def test_sample_rate_change_rebuilds_without_error():
    effect = RobotEffect(strength=50)
    pcm24 = _tone_mix(24000)
    pcm16 = _tone_mix(16000)
    assert len(effect.process(pcm24, 24000)) == len(pcm24)
    assert len(effect.process(pcm16, 16000)) == len(pcm16)


def test_strength_and_tone_are_clamped():
    effect = RobotEffect(strength=250, tone_hz=0.0)
    assert effect.strength == 100
    assert effect.tone_hz == 1.0


def test_effect_from_config():
    assert effect_from_config("none", 50, 40.0) is None
    assert effect_from_config("mystery", 50, 40.0) is None
    robot = effect_from_config("robot", 30, 55.0)
    assert isinstance(robot, RobotEffect)
    assert robot.strength == 30 and robot.tone_hz == 55.0
