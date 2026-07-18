from richard.setup.detect import (
    HardwareProfile,
    parse_vram_mb,
    parse_meminfo_mb,
    detect_hardware,
)


def test_parse_vram_reads_first_gpu_megabytes():
    assert parse_vram_mb("24576\n8192\n") == 24576


def test_parse_vram_empty_means_zero():
    assert parse_vram_mb("") == 0
    assert parse_vram_mb("\n") == 0


def test_parse_meminfo_converts_kb_to_mb():
    text = "MemTotal:       65854560 kB\nMemFree: 100 kB\n"
    assert parse_meminfo_mb(text) == 64311  # 65854560 // 1024


def test_parse_meminfo_missing_returns_zero():
    assert parse_meminfo_mb("Bogus: 1 kB\n") == 0


def test_detect_uses_injected_collaborators_gpu_present():
    profile = detect_hardware(
        nvidia_smi=lambda: "12288\n",
        cpu_count=lambda: 16,
        meminfo_text=lambda: "MemTotal: 33554432 kB\n",
    )
    assert profile == HardwareProfile(has_gpu=True, vram_mb=12288, cpu_cores=16, ram_mb=32768)


def test_detect_gpu_absent_when_nvidia_smi_raises():
    def boom():
        raise FileNotFoundError("nvidia-smi not found")

    profile = detect_hardware(
        nvidia_smi=boom,
        cpu_count=lambda: 4,
        meminfo_text=lambda: "MemTotal: 8388608 kB\n",
    )
    assert profile.has_gpu is False
    assert profile.vram_mb == 0
    assert profile.cpu_cores == 4
