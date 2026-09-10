import shutil
import subprocess
import zipfile
from importlib import resources
from pathlib import Path

from test_web import _app


def test_realtime_client_asset_is_served_as_javascript(tmp_path):
    response = _app(tmp_path).handle("GET", "/realtime-client.js")

    assert response.status == 200
    assert response.content_type == "text/javascript; charset=utf-8"
    assert response.headers == {"Cache-Control": "no-store"}
    assert response.body == resources.files("richard.web").joinpath("realtime_client.js").read_bytes()


def test_home_loads_realtime_client_before_the_spa_handler(tmp_path):
    html = _app(tmp_path).handle("GET", "/").body.decode()

    module = html.index('<script src="/realtime-client.js"></script>')
    spa = html.index("<script>", module)
    assert module < spa


def test_built_wheel_contains_realtime_client(tmp_path):
    repository = Path(__file__).resolve().parents[1]
    source = tmp_path / "source"
    shutil.copytree(
        repository,
        source,
        ignore=shutil.ignore_patterns(".git", ".venv", "build", "dist", "*.egg-info", "__pycache__"),
    )
    wheelhouse = tmp_path / "wheelhouse"
    wheelhouse.mkdir()
    subprocess.run(
        [
            "uv",
            "build",
            "--wheel",
            "--out-dir",
            str(wheelhouse),
            ".",
        ],
        cwd=source,
        check=True,
        capture_output=True,
        text=True,
    )
    wheel = next(wheelhouse.glob("*.whl"))

    with zipfile.ZipFile(wheel) as archive:
        assert "richard/web/realtime_client.js" in archive.namelist()
