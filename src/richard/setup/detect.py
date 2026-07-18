from __future__ import annotations

import os
import subprocess
from dataclasses import dataclass
from typing import Callable


@dataclass(frozen=True)
class HardwareProfile:
    has_gpu: bool
    vram_mb: int
    cpu_cores: int
    ram_mb: int


def parse_vram_mb(nvidia_smi_output: str) -> int:
    """First GPU's total VRAM in MB from `nvidia-smi --query-gpu=memory.total`."""
    for line in nvidia_smi_output.splitlines():
        line = line.strip()
        if line:
            try:
                return int(line)
            except ValueError:
                return 0
    return 0


def parse_meminfo_mb(meminfo_text: str) -> int:
    """Total RAM in MB from /proc/meminfo MemTotal (which is in kB)."""
    for line in meminfo_text.splitlines():
        if line.startswith("MemTotal:"):
            parts = line.split()
            if len(parts) >= 2 and parts[1].isdigit():
                return int(parts[1]) // 1024
    return 0


def _run_nvidia_smi() -> str:
    return subprocess.run(
        ["nvidia-smi", "--query-gpu=memory.total", "--format=csv,noheader,nounits"],
        capture_output=True,
        text=True,
        check=True,
    ).stdout


def _read_meminfo() -> str:
    try:
        with open("/proc/meminfo", encoding="utf-8") as f:
            return f.read()
    except OSError:
        return ""


def detect_hardware(
    nvidia_smi: Callable[[], str] = _run_nvidia_smi,
    cpu_count: Callable[[], int | None] = os.cpu_count,
    meminfo_text: Callable[[], str] = _read_meminfo,
) -> HardwareProfile:
    try:
        vram_mb = parse_vram_mb(nvidia_smi())
    except (OSError, subprocess.SubprocessError):
        vram_mb = 0
    return HardwareProfile(
        has_gpu=vram_mb > 0,
        vram_mb=vram_mb,
        cpu_cores=cpu_count() or 1,
        ram_mb=parse_meminfo_mb(meminfo_text()),
    )
