import os
import shutil
import subprocess
from pathlib import Path

import pytest


SCRIPT = Path(__file__).resolve().parent.parent / "update.sh"


def _run(command, *, cwd=None, env=None):
    try:
        return subprocess.run(
            command,
            cwd=cwd,
            env=env,
            check=True,
            capture_output=True,
            text=True,
        )
    except subprocess.CalledProcessError as exc:
        raise AssertionError(
            f"command failed ({exc.returncode}): {command}\n"
            f"stdout:\n{exc.stdout}\nstderr:\n{exc.stderr}"
        ) from exc


def _git(repo: Path, *args: str):
    return _run(["git", *args], cwd=repo)


def _commit(repo: Path, message: str) -> str:
    _git(repo, "add", ".")
    _git(repo, "commit", "-m", message)
    return _git(repo, "rev-parse", "HEAD").stdout.strip()


def _minimal_project(repo: Path, version: str) -> None:
    (repo / "pyproject.toml").write_text(
        """\
[build-system]
requires = ["setuptools>=61"]
build-backend = "setuptools.build_meta"

[project]
name = "richard-update-fixture"
version = "0.0.1"

[project.scripts]
richard = "richard_update_fixture:main"

[tool.setuptools]
py-modules = ["richard_update_fixture"]
""",
        encoding="utf-8",
    )
    (repo / "richard_update_fixture.py").write_text(
        f'def main():\n    print("richard fixture {version}")\n', encoding="utf-8"
    )
    units = repo / "richard" / "setup" / "units.py"
    units.parent.mkdir(parents=True, exist_ok=True)
    (units.parent.parent / "__init__.py").write_text("", encoding="utf-8")
    (units.parent / "__init__.py").write_text("", encoding="utf-8")
    units.write_text(
        """\
def render_chatterbox_unit(*, workdir, python, gpu):
    return f"WorkingDirectory={workdir}\\nEnvironment=CUDA_VISIBLE_DEVICES={gpu}\\nExecStart={python} server.py\\n"
""",
        encoding="utf-8",
    )
    (units.parent / "deployment.py").write_text(
        """\
import os
import sys

if __name__ == "__main__":
    with open(os.environ["RICHARD_FAKE_DEPLOYMENT_LOG"], "a", encoding="utf-8") as log:
        log.write(
            f"{sys.argv[1]}:{os.environ.get('CUDA_VISIBLE_DEVICES', '')}:"
            f"{os.environ.get('LD_LIBRARY_PATH', '')}\\n"
        )
""",
        encoding="utf-8",
    )
    (units.parent / "cuda.py").write_text(
        """\
import os

if __name__ == "__main__":
    print(os.environ["RICHARD_FAKE_CUDA_LIBRARY_PATH"])
""",
        encoding="utf-8",
    )
    voice_asset = repo / "assets" / "voice" / "richard-voice.wav"
    voice_asset.parent.mkdir(parents=True, exist_ok=True)
    voice_asset.write_text(f"voice {version}\n", encoding="utf-8")


def _deployment(tmp_path: Path):
    remote = tmp_path / "remote.git"
    publisher = tmp_path / "publisher"
    deploy = tmp_path / "deploy"
    _run(["git", "init", "--bare", "--initial-branch=main", str(remote)])
    _run(["git", "init", "--initial-branch=main", str(publisher)])
    _git(publisher, "config", "user.name", "Richard Update Test")
    _git(publisher, "config", "user.email", "update-test@example.invalid")
    _minimal_project(publisher, "v1")
    old_commit = _commit(publisher, "initial")
    _git(publisher, "remote", "add", "origin", str(remote))
    _git(publisher, "push", "-u", "origin", "main")
    _run(["git", "clone", str(remote), str(deploy)])
    (deploy / ".venv" / "bin").mkdir(parents=True)
    return remote, publisher, deploy, old_commit


def _update_env(tmp_path: Path, deploy: Path) -> dict[str, str]:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir(exist_ok=True)
    fake_uv = bin_dir / "uv"
    fake_uv.write_text(
        """#!/usr/bin/env python3
import os
import pathlib
import sys

if sys.argv[1] == "venv":
    if os.environ.get("RICHARD_FAKE_UV_FAIL") == "1":
        raise SystemExit(9)
    target = pathlib.Path(sys.argv[-1])
    (target / "bin").mkdir(parents=True)
    os.symlink(sys.executable, target / "bin" / "python")
elif sys.argv[1:3] == ["pip", "install"]:
    python = pathlib.Path(sys.argv[sys.argv.index("--python") + 1])
    command = python.parent / "richard"
    command.write_text("#!/bin/sh\\necho 'richard fixture v2'\\n", encoding="utf-8")
    command.chmod(0o755)
else:
    raise SystemExit(f"unexpected fake uv arguments: {sys.argv[1:]}")
""",
        encoding="utf-8",
    )
    fake_uv.chmod(0o755)
    env = dict(os.environ)
    env.update(
        {
            "RICHARD_HOME": str(deploy),
            "TTS_ROOT": str(tmp_path / "voice"),
            "UNIT_DIR": str(tmp_path / "units"),
            "RICHARD_UPDATE_LOCK": str(tmp_path / "update.lock"),
            "RICHARD_UPDATE_BACKUP_ROOT": str(tmp_path / "backups"),
            "RICHARD_UPDATE_ALLOW_NON_ROOT": "1",
            "RICHARD_SERVICE_NAMES": "",
            "RICHARD_BIN_DIR": str(tmp_path / "global-bin"),
            "RICHARD_FAKE_DEPLOYMENT_LOG": str(tmp_path / "deployment.log"),
            "RICHARD_FAKE_CUDA_LIBRARY_PATH": "/fake/cuda/lib",
            "LD_LIBRARY_PATH": "",
            "PATH": f"{bin_dir}{os.pathsep}{env['PATH']}",
        }
    )
    return env


def _install_mock_tts_service(
    tmp_path: Path, env: dict[str, str], *, healthy: bool = True
) -> tuple[Path, Path]:
    voice = tmp_path / "voice"
    units = tmp_path / "units"
    log = tmp_path / "systemctl.log"
    pip = voice / "venv" / "bin" / "pip"
    pip.parent.mkdir(parents=True)
    pip.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    pip.chmod(0o755)
    reference = voice / "Chatterbox-TTS-Server" / "reference_audio" / "richard-voice.wav"
    reference.parent.mkdir(parents=True)
    reference.write_text("voice v1\n", encoding="utf-8")
    (reference.parent.parent / "requirements-nvidia.txt").write_text("", encoding="utf-8")
    units.mkdir()
    (units / "chatterbox-tts.service").write_text(
        "Environment=CUDA_VISIBLE_DEVICES=2\n", encoding="utf-8"
    )
    bin_dir = Path(env["PATH"].split(os.pathsep, 1)[0])
    systemctl = bin_dir / "systemctl"
    systemctl.write_text(
        "#!/bin/sh\nprintf '%s\\n' \"$*\" >> \"$RICHARD_FAKE_SYSTEMCTL_LOG\"\nexit 0\n",
        encoding="utf-8",
    )
    systemctl.chmod(0o755)
    curl = bin_dir / "curl"
    curl.write_text(
        f"#!/bin/sh\n{'printf 200' if healthy else 'exit 1'}\n", encoding="utf-8"
    )
    curl.chmod(0o755)
    env["RICHARD_FAKE_SYSTEMCTL_LOG"] = str(log)
    env["RICHARD_UPDATE_HEALTH_ATTEMPTS"] = "1"
    env["RICHARD_UPDATE_HEALTH_DELAY"] = "0"
    return reference, log


def test_update_sh_exists_is_executable_and_parses():
    assert SCRIPT.exists()
    assert os.access(SCRIPT, os.X_OK)
    _run(["bash", "-n", str(SCRIPT)])


def test_update_sh_passes_shellcheck():
    if not shutil.which("shellcheck"):
        pytest.skip("shellcheck not installed")
    result = subprocess.run(["shellcheck", str(SCRIPT)], capture_output=True, text=True)
    assert result.returncode == 0, result.stdout + result.stderr


def test_help_exposes_tts_options_only():
    result = _run([str(SCRIPT), "--help"])
    assert "--tts-deps" in result.stdout
    assert "--skip-tts" in result.stdout
    assert "--voice-deps" not in result.stdout
    assert "--skip-voice" not in result.stdout


def test_check_reports_fast_forward_without_changing_deployment(tmp_path):
    _remote, publisher, deploy, old_commit = _deployment(tmp_path)
    _git(publisher, "commit", "--allow-empty", "-m", "available update")
    target = _git(publisher, "rev-parse", "HEAD").stdout.strip()
    _git(publisher, "push", "origin", "main")

    result = _run([str(SCRIPT), "--check"], env=_update_env(tmp_path, deploy))

    assert "Update available: 1 commit" in result.stdout
    assert old_commit[:12] in result.stdout and target[:12] in result.stdout
    assert _git(deploy, "rev-parse", "HEAD").stdout.strip() == old_commit
    assert not (tmp_path / "update.lock").exists()


def test_full_update_switches_versioned_venv_and_advances_git(tmp_path):
    _remote, publisher, deploy, _old_commit = _deployment(tmp_path)
    _minimal_project(publisher, "v2")
    target = _commit(publisher, "release v2")
    _git(publisher, "push", "origin", "main")

    result = _run([str(SCRIPT)], env=_update_env(tmp_path, deploy))

    assert "updated successfully" in result.stdout
    assert _git(deploy, "rev-parse", "HEAD").stdout.strip() == target
    assert (deploy / ".venv").is_symlink()
    assert (deploy / ".venvs" / target / "bin" / "richard").exists()
    command_link = tmp_path / "global-bin" / "richard"
    assert command_link.is_symlink()
    assert command_link.resolve() == (deploy / ".venvs" / target / "bin" / "richard")
    smoke = _run([str(deploy / ".venv" / "bin" / "richard")])
    assert "richard fixture v2" in smoke.stdout


def test_failed_environment_build_rolls_git_back_and_releases_lock(tmp_path):
    _remote, publisher, deploy, old_commit = _deployment(tmp_path)
    _minimal_project(publisher, "broken")
    _commit(publisher, "broken release")
    _git(publisher, "push", "origin", "main")
    env = _update_env(tmp_path, deploy)
    env["RICHARD_FAKE_UV_FAIL"] = "1"

    result = subprocess.run(
        [str(SCRIPT)], env=env, capture_output=True, text=True, check=False
    )

    assert result.returncode != 0
    assert "rolling back" in result.stderr.lower()
    assert _git(deploy, "rev-parse", "HEAD").stdout.strip() == old_commit
    assert (deploy / ".venv").is_dir() and not (deploy / ".venv").is_symlink()
    assert not (tmp_path / "update.lock").exists()


def test_tts_assets_unit_and_active_service_are_updated(tmp_path):
    _remote, publisher, deploy, _old_commit = _deployment(tmp_path)
    _minimal_project(publisher, "v2")
    _commit(publisher, "voice release")
    _git(publisher, "push", "origin", "main")
    env = _update_env(tmp_path, deploy)
    reference, systemctl_log = _install_mock_tts_service(tmp_path, env)

    result = _run([str(SCRIPT)], env=env)

    assert "Refreshing Richard-managed GPU TTS service" in result.stdout
    assert reference.read_text(encoding="utf-8") == "voice v2\n"
    assert "CUDA_VISIBLE_DEVICES=2" in (tmp_path / "units" / "chatterbox-tts.service").read_text()
    lifecycle = systemctl_log.read_text(encoding="utf-8")
    assert "stop chatterbox-tts.service" in lifecycle
    assert "start chatterbox-tts.service" in lifecycle
    assert "whisper-stt.service" not in lifecycle


def test_tts_health_failure_restores_assets_unit_git_and_venv(tmp_path):
    _remote, publisher, deploy, old_commit = _deployment(tmp_path)
    old_chatterbox_unit = "Environment=CUDA_VISIBLE_DEVICES=2\n"
    _minimal_project(publisher, "v2")
    _commit(publisher, "unhealthy voice release")
    _git(publisher, "push", "origin", "main")
    env = _update_env(tmp_path, deploy)
    reference, _log = _install_mock_tts_service(tmp_path, env, healthy=False)

    result = subprocess.run(
        [str(SCRIPT)], env=env, capture_output=True, text=True, check=False
    )

    assert result.returncode != 0
    assert "rolling back" in result.stderr.lower()
    assert _git(deploy, "rev-parse", "HEAD").stdout.strip() == old_commit
    assert (deploy / ".venv").is_symlink()
    assert (deploy / ".venv").resolve().name.startswith(f"legacy-{old_commit[:12]}-")
    assert reference.read_text(encoding="utf-8") == "voice v1\n"
    assert (tmp_path / "units" / "chatterbox-tts.service").read_text() == old_chatterbox_unit


def test_active_richard_update_prepares_models_and_verifies_realtime(tmp_path):
    _remote, publisher, deploy, _old_commit = _deployment(tmp_path)
    _minimal_project(publisher, "v2")
    _commit(publisher, "realtime release")
    _git(publisher, "push", "origin", "main")
    env = _update_env(tmp_path, deploy)
    _reference, _systemctl_log = _install_mock_tts_service(tmp_path, env)
    (tmp_path / "units" / "richard.service").write_text(
        "Environment=CUDA_VISIBLE_DEVICES=3\n", encoding="utf-8"
    )
    env["RICHARD_SERVICE_NAMES"] = "richard.service"

    _run([str(SCRIPT)], env=env)

    actions = (tmp_path / "deployment.log").read_text(encoding="utf-8").splitlines()
    assert actions == ["prepare:3:/fake/cuda/lib", "verify::"]
