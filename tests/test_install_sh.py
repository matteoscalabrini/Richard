import shutil
import subprocess
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parent.parent / "install.sh"


def test_install_sh_exists():
    assert SCRIPT.exists()


def test_install_sh_passes_shellcheck():
    if not shutil.which("shellcheck"):
        pytest.skip("shellcheck not installed")
    result = subprocess.run(["shellcheck", str(SCRIPT)], capture_output=True, text=True)
    assert result.returncode == 0, result.stdout + result.stderr


def test_install_sh_installs_required_system_packages():
    text = SCRIPT.read_text()
    for pkg in ["build-essential", "ffmpeg", "libsndfile1", "portaudio19-dev", "espeak-ng"]:
        assert pkg in text, f"missing apt package: {pkg}"


def test_install_sh_uses_uv_for_python_311_and_hands_off():
    text = SCRIPT.read_text()
    assert "uv" in text
    assert "3.11" in text
    assert "richard setup" in text  # hands off to the Python configurator


def test_install_sh_is_rerunnable_and_advertises_updater():
    text = SCRIPT.read_text()
    assert "if [ ! -x .venv/bin/python ]" in text
    assert "--upgrade -e" in text
    assert "update.sh" in text
    assert ".richard-install" in text
    assert 'ln -sfn "$RICHARD_HOME/.venv/bin/richard"' in text
    assert '"$RICHARD_BIN_DIR/richard"' in text
    assert "/etc/systemd/system/richard.service" in text
    assert 'ExecStart=$RICHARD_HOME/.venv/bin/richard serve' in text
    assert "systemctl enable richard.service" in text
    assert "systemctl restart richard.service" in text


def test_install_sh_prepares_and_verifies_realtime_models():
    text = SCRIPT.read_text()
    assert '-m richard.setup.cuda "$TTS_ROOT/venv"' in text
    assert 'LD_LIBRARY_PATH="$RICHARD_LD_LIBRARY_PATH"' in text
    assert "-m richard.setup.deployment prepare" in text
    assert "-m richard.setup.deployment verify" in text


def test_install_sh_service_owns_realtime_voice_on_selected_gpu():
    text = SCRIPT.read_text()
    assert "Description=Richard realtime voice, relay, and web host" in text
    assert "Environment=CUDA_VISIBLE_DEVICES=$RICHARD_GPU" in text
    assert "Environment=LD_LIBRARY_PATH=$RICHARD_LD_LIBRARY_PATH" in text
    assert "After=network-online.target chatterbox-tts.service" in text
    assert "whisper-stt.service" not in text
    assert "assets/voice-server" not in text


def test_install_sh_records_new_deployment_layout():
    text = SCRIPT.read_text()
    assert "gpu=$RICHARD_GPU" in text
    assert "tts_root=$TTS_ROOT" in text
    assert "voice_root=" not in text
