from richard.setup.plan import plan_install
from richard.setup.detect import HardwareProfile
from richard.setup.services import provision_voice_services


def _gpu_plan():
    profile = HardwareProfile(has_gpu=True, vram_mb=24576, cpu_cores=8, ram_mb=16000)
    return plan_install(profile)


def test_provision_runs_clone_install_and_writes_chatterbox_unit_only():
    commands = []
    units = {}
    provision_voice_services(
        _gpu_plan(),
        gpu="1",
        run=lambda cmd, **kw: commands.append(cmd),
        write_unit=lambda name, text: units.__setitem__(name, text),
        reference_clip="/opt/richard/assets/voice/richard-voice.wav",
        exists=lambda path: False,
    )
    joined = " ".join(" ".join(c) for c in commands)
    assert "Chatterbox-TTS-Server" in joined  # repo cloned
    assert "chatterbox-v2" in joined  # the engine package itself is installed (else ModuleNotFound)
    assert any("enable" in " ".join(c) for c in commands)  # systemctl enable
    assert "faster-whisper" not in joined
    assert "uvicorn" not in joined
    assert set(units) == {"chatterbox-tts.service"}
    assert any(
        command == ["systemctl", "restart", "chatterbox-tts.service"]
        for command in commands
    )


def test_provision_deploys_reference_clip():
    commands = []
    provision_voice_services(
        _gpu_plan(),
        gpu="1",
        run=lambda cmd, **kw: commands.append(cmd),
        write_unit=lambda name, text: None,
        reference_clip="/opt/richard/assets/voice/richard-voice.wav",
        exists=lambda path: False,
    )
    joined = " ".join(" ".join(c) for c in commands)
    assert "richard-voice.wav" in joined
    assert "reference_audio" in joined


def test_provision_noop_when_no_services():
    from richard.setup.detect import HardwareProfile

    cpu_plan = plan_install(HardwareProfile(False, 0, 8, 16000))
    commands = []
    provision_voice_services(
        cpu_plan,
        gpu="0",
        run=lambda cmd, **kw: commands.append(cmd),
        write_unit=lambda name, text: None,
        reference_clip="/x.wav",
        exists=lambda path: False,
    )
    assert commands == []


def test_provision_reuses_existing_voice_venv_and_chatterbox_checkout():
    commands = []
    provision_voice_services(
        _gpu_plan(),
        gpu="1",
        run=lambda cmd, **kw: commands.append(cmd),
        write_unit=lambda name, text: None,
        reference_clip="/opt/richard/assets/voice/richard-voice.wav",
        exists=lambda path: path.endswith("/bin/python") or path.endswith("/.git"),
    )
    joined = "\n".join(" ".join(command) for command in commands)
    assert "python3 -m venv" not in joined
    assert "git clone" not in joined
    assert "systemctl restart chatterbox-tts.service" in joined
    assert "whisper-stt.service" not in joined


def test_provision_preserves_incomplete_chatterbox_directory_before_clone():
    commands = []
    messages = []
    chatterbox = "/opt/voice/Chatterbox-TTS-Server"
    provision_voice_services(
        _gpu_plan(),
        gpu="1",
        run=lambda cmd, **kw: commands.append(cmd),
        write_unit=lambda name, text: None,
        reference_clip="/opt/richard/assets/voice/richard-voice.wav",
        exists=lambda path: path in {f"{chatterbox}", "/opt/voice/venv/bin/python"},
        notify=messages.append,
    )

    move = next(command for command in commands if command[0] == "mv")
    clone = next(command for command in commands if command[:2] == ["git", "clone"])
    assert move[1] == chatterbox
    assert move[2].startswith(f"{chatterbox}.incomplete-")
    assert clone[-1] == chatterbox
    assert move[2] in messages[0]


def test_provision_accepts_complete_archive_checkout_without_git_metadata():
    commands = []
    chatterbox = "/opt/voice/Chatterbox-TTS-Server"
    provision_voice_services(
        _gpu_plan(),
        gpu="1",
        run=lambda cmd, **kw: commands.append(cmd),
        write_unit=lambda name, text: None,
        reference_clip="/opt/richard/assets/voice/richard-voice.wav",
        exists=lambda path: path
        in {
            "/opt/voice/venv/bin/python",
            f"{chatterbox}/server.py",
            f"{chatterbox}/requirements-nvidia.txt",
        },
    )

    joined = "\n".join(" ".join(command) for command in commands)
    assert "git clone" not in joined
    assert "mv /opt/voice/Chatterbox-TTS-Server" not in joined
