from richard.config import load_config
from richard.setup import run_setup
from richard.setup.detect import HardwareProfile


def test_cpu_path_writes_config_and_skips_services(tmp_path):
    cfg_path = tmp_path / "config.toml"
    provisioned = []
    lines = []
    run_setup(
        config_path=cfg_path,
        detect=lambda: HardwareProfile(False, 0, 8, 16000),
        configure_llm=lambda: ("http://localhost:8080", None, "local"),
        provision=lambda plan, write: provisioned.append(plan),
        check=lambda targets: [],
        gpu="1",
        write=lines.append,
    )
    assert provisioned == []  # CPU path: no services
    saved = load_config(cfg_path)
    assert saved.voice.tts_engine == "kokoro"
    assert saved.voice.stt_engine == "local"
    assert saved.llm_endpoint == "http://localhost:8080"


def test_gpu_path_provisions_and_writes_realtime_config(tmp_path):
    cfg_path = tmp_path / "config.toml"
    provisioned = []
    run_setup(
        config_path=cfg_path,
        detect=lambda: HardwareProfile(True, 24576, 8, 16000),
        configure_llm=lambda: ("http://10.0.0.5:8080", "sk-x", "Gemma4"),
        provision=lambda plan, write: provisioned.append(plan),
        check=lambda targets: [],
        gpu="1",
        write=lambda s: None,
    )
    assert len(provisioned) == 1 and provisioned[0].needs_services
    saved = load_config(cfg_path)
    assert saved.voice.tts_engine == "remote"
    assert saved.voice.tts_endpoint == "http://127.0.0.1:8004"
    assert saved.voice.stt_engine == "local"
    assert saved.voice.stt_model == "large-v3-turbo"
    assert saved.voice.stt_endpoint == ""
    assert saved.llm_api_key == "sk-x"
    assert saved.llm_model == "Gemma4"


def test_gpu_path_health_checks_llm_and_tts_without_stt_service(tmp_path):
    checked = []
    run_setup(
        config_path=tmp_path / "c.toml",
        detect=lambda: HardwareProfile(True, 24576, 8, 16000),
        configure_llm=lambda: ("http://localhost:8080", None, "local"),
        provision=lambda plan, write: None,
        check=lambda targets: checked.extend(targets) or [],
        gpu="1",
        write=lambda s: None,
    )

    assert checked == [
        ("LLM", "http://localhost:8080/v1/models"),
        ("TTS", "http://127.0.0.1:8004"),
    ]


def test_override_cpu_forces_cpu(tmp_path):
    run_setup(
        config_path=tmp_path / "c.toml",
        detect=lambda: HardwareProfile(True, 24576, 8, 16000),
        configure_llm=lambda: ("http://localhost:8080", None, "local"),
        provision=lambda plan, write: (_ for _ in ()).throw(AssertionError("must not provision")),
        check=lambda targets: [],
        gpu="1",
        override="cpu",
        write=lambda s: None,
    )
    assert load_config(tmp_path / "c.toml").voice.tts_engine == "kokoro"
