import shutil
import subprocess
import sys
import venv
from pathlib import Path

import pytest

from richard import cli
from richard.config import Config, load_config, save_config


def test_plugins_list_shows_installed_and_state(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("HOME", str(tmp_path))
    assert cli.main(["plugins", "list"]) == 0
    out = capsys.readouterr().out
    assert "home_assistant" in out
    assert "disabled" in out


def test_plugins_enable_and_disable_persist(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    assert cli.main(["plugins", "enable", "home_assistant"]) == 0
    assert load_config().plugins.enabled == ["home_assistant"]
    assert cli.main(["plugins", "disable", "home_assistant"]) == 0
    assert load_config().plugins.enabled == []


def test_plugins_enable_unknown_name_fails(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("HOME", str(tmp_path))
    assert cli.main(["plugins", "enable", "ghost"]) == 1
    assert "not installed" in capsys.readouterr().out


def test_plugins_config_writes_typed_values(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    assert cli.main(["plugins", "config", "home_assistant", "host=ha.local", "port=9443", "use_https=on", "timeout=2.5"]) == 0
    assert load_config().plugins.tables["home_assistant"] == {"host": "ha.local", "port": 9443, "use_https": True, "timeout": 2.5}


def test_parse_plugin_value():
    assert cli._parse_plugin_value("on") is True
    assert cli._parse_plugin_value("false") is False
    assert cli._parse_plugin_value("12") == 12
    assert cli._parse_plugin_value("1.5") == 1.5
    assert cli._parse_plugin_value("ha.local") == "ha.local"


def test_set_ha_flags_are_aliases_of_plugins_config(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    assert cli.main(["config", "--set-ha-host", "ha.local", "--set-ha-enabled", "on"]) == 0
    config = load_config()
    assert config.plugins.enabled == ["home_assistant"]
    assert config.plugins.tables["home_assistant"]["host"] == "ha.local"


def _venv_with_pip(env_dir: Path) -> Path:
    """A fresh venv that has pip. Prefers the stdlib; falls back to uv, because some
    uv-managed Pythons abort in ensurepip when it is spawned from a new venv."""
    try:
        venv.create(env_dir, with_pip=True)
    except subprocess.CalledProcessError:
        uv = shutil.which("uv")
        if uv is None:
            pytest.skip("ensurepip is broken for this interpreter and uv is not installed")
        shutil.rmtree(env_dir, ignore_errors=True)
        subprocess.run([uv, "venv", "-q", "--seed", "--python", sys.executable, str(env_dir)], check=True)
    return env_dir / "bin" / "python"


@pytest.mark.slow
def test_plugins_install_editable_folder_registers_an_entry_point(tmp_path):
    folder = tmp_path / "richard-plugin-demo"
    (folder / "richard_plugin_demo").mkdir(parents=True)
    (folder / "richard_plugin_demo" / "__init__.py").write_text(
        "class DemoPlugin:\n    name = 'demo'\n    version = '0.1'\n"
        "    def config_defaults(self):\n        return {}\n"
        "    def build(self, ctx):\n        from richard.plugins.base import PluginParts\n        return PluginParts(context='Demo here.')\n"
    )
    (folder / "pyproject.toml").write_text(
        '[build-system]\nrequires = ["setuptools>=77"]\nbuild-backend = "setuptools.build_meta"\n\n'
        '[project]\nname = "richard-plugin-demo"\nversion = "0.1"\n\n'
        '[project.entry-points."richard.plugins"]\ndemo = "richard_plugin_demo:DemoPlugin"\n'
    )
    python = _venv_with_pip(tmp_path / "venv")
    subprocess.run([str(python), "-m", "pip", "install", "-q", "-e", str(folder)], check=True)
    probe = "from importlib import metadata; print([e.name for e in metadata.entry_points(group='richard.plugins')])"
    out = subprocess.run([str(python), "-c", probe], check=True, capture_output=True, text=True).stdout
    assert "demo" in out
    assert cli._install_command(folder, python=str(python)) == [str(python), "-m", "pip", "install", "-e", str(folder)]
