from __future__ import annotations

from dataclasses import dataclass

from richard.config import Config
from richard.setup.detect import HardwareProfile

# VRAM ladder constants (MB). v1 live forks at 4 GB and 8 GB; finer rungs
# (12/16/24 GB) collapse to gpu-full because the voice stack maxes out ~8 GB.
VRAM_GPU_MIN_MB = 4000
VRAM_GPU_FULL_MB = 8000

TTS_ENDPOINT = "http://127.0.0.1:8004"
RICHARD_VOICE = "richard-voice.wav"


@dataclass(frozen=True)
class InstallPlan:
    rung: str  # "cpu" | "gpu-small" | "gpu-full"
    needs_services: bool
    tts_engine: str  # "kokoro" | "remote"
    tts_voice: str  # "bm_lewis" | "richard-voice.wav"
    tts_endpoint: str  # "" | TTS_ENDPOINT
    stt_engine: str  # "local" | "remote"
    stt_model: str  # local faster-whisper model
    stt_endpoint: str  # empty for the in-process realtime stack


_CPU_PLAN = InstallPlan(
    rung="cpu",
    needs_services=False,
    tts_engine="kokoro",
    tts_voice="bm_lewis",
    tts_endpoint="",
    stt_engine="local",
    stt_model="base.en",
    stt_endpoint="",
)


def _gpu_plan(rung: str) -> InstallPlan:
    return InstallPlan(
        rung=rung,
        needs_services=True,
        tts_engine="remote",
        tts_voice=RICHARD_VOICE,
        tts_endpoint=TTS_ENDPOINT,
        stt_engine="local",
        stt_model="large-v3-turbo",
        stt_endpoint="",
    )


def plan_install(profile: HardwareProfile, override: str | None = None) -> InstallPlan:
    """Map a hardware profile onto the VRAM ladder. `override` ('cpu'|'gpu') is the
    hidden escape hatch — never a normal user choice."""
    if override == "cpu":
        return _CPU_PLAN
    if override == "gpu":
        # Forced GPU path: default to the full voice stack regardless of detected VRAM.
        return _gpu_plan("gpu-full")
    if profile.vram_mb >= VRAM_GPU_FULL_MB:
        return _gpu_plan("gpu-full")
    if profile.vram_mb >= VRAM_GPU_MIN_MB:
        return _gpu_plan("gpu-small")
    return _CPU_PLAN


def apply_plan(plan: InstallPlan, config: Config) -> None:
    """Write the plan's engine choices onto a Config's voice section (in place)."""
    config.voice.tts_engine = plan.tts_engine
    config.voice.tts_voice = plan.tts_voice
    config.voice.tts_endpoint = plan.tts_endpoint
    config.voice.stt_engine = plan.stt_engine
    config.voice.stt_model = plan.stt_model
    config.voice.stt_endpoint = plan.stt_endpoint
