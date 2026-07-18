import pytest

from richard.config import Config
from richard.setup.detect import HardwareProfile
from richard.setup.plan import InstallPlan, plan_install, apply_plan


def _profile(vram, has_gpu=None):
    return HardwareProfile(
        has_gpu=has_gpu if has_gpu is not None else vram > 0,
        vram_mb=vram, cpu_cores=8, ram_mb=16000,
    )


def test_no_gpu_is_cpu_rung_no_services():
    plan = plan_install(_profile(0))
    assert plan.rung == "cpu"
    assert plan.needs_services is False
    assert plan.tts_engine == "kokoro"
    assert plan.stt_engine == "local"
    assert plan.stt_model == "base.en"
    assert plan.tts_endpoint == "" and plan.stt_endpoint == ""


def test_small_gpu_uses_local_realtime_stt_and_chatterbox():
    plan = plan_install(_profile(6000))
    assert plan.rung == "gpu-small"
    assert plan.needs_services is True
    assert plan.tts_engine == "remote"
    assert plan.tts_voice == "richard-voice.wav"
    assert plan.tts_endpoint == "http://127.0.0.1:8004"
    assert plan.stt_engine == "local"
    assert plan.stt_model == "large-v3-turbo"
    assert plan.stt_endpoint == ""


def test_big_gpu_uses_local_realtime_stt_and_chatterbox():
    plan = plan_install(_profile(24576))
    assert plan.rung == "gpu-full"
    assert plan.needs_services is True
    assert plan.stt_engine == "local"
    assert plan.stt_model == "large-v3-turbo"
    assert plan.stt_endpoint == ""
    assert plan.tts_engine == "remote"


@pytest.mark.parametrize(
    "vram,expected",
    [(0, "cpu"), (3999, "cpu"), (4000, "gpu-small"), (7999, "gpu-small"), (8000, "gpu-full")],
)
def test_ladder_boundaries(vram, expected):
    assert plan_install(_profile(vram)).rung == expected


def test_override_cpu_forces_cpu_even_with_big_gpu():
    assert plan_install(_profile(24576), override="cpu").rung == "cpu"


def test_override_gpu_forces_full_when_no_gpu_detected():
    plan = plan_install(_profile(0), override="gpu")
    assert plan.rung == "gpu-full"
    assert plan.needs_services is True


def test_apply_plan_writes_voice_config():
    cfg = Config()
    plan = plan_install(_profile(24576))
    apply_plan(plan, cfg)
    assert cfg.voice.tts_engine == "remote"
    assert cfg.voice.tts_voice == "richard-voice.wav"
    assert cfg.voice.tts_endpoint == "http://127.0.0.1:8004"
    assert cfg.voice.stt_engine == "local"
    assert cfg.voice.stt_model == "large-v3-turbo"
    assert cfg.voice.stt_endpoint == ""


def test_apply_plan_cpu_sets_local_engines():
    cfg = Config()
    apply_plan(plan_install(_profile(0)), cfg)
    assert cfg.voice.tts_engine == "kokoro"
    assert cfg.voice.stt_engine == "local"
    assert cfg.voice.stt_model == "base.en"
