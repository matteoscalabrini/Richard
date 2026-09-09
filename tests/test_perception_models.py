import pytest

from richard.perception.models import MODELS, ensure_model, session_options


class _Resp:
    def __init__(self, content, status=200):
        self.content = content
        self.status = status

    def raise_for_status(self):
        if self.status != 200:
            raise RuntimeError(f"http {self.status}")


class _Client:
    def __init__(self, content=b"x" * 2_000_000, status=200):
        self.calls = []
        self._content = content
        self._status = status

    def get(self, url):
        self.calls.append(url)
        return _Resp(self._content, self._status)


def test_registry_lists_the_three_models_with_permissive_licences():
    assert set(MODELS) == {"ssd_mobilenet_v1", "ultraface_rfb_320", "arcface_r100_int8"}
    assert MODELS["ssd_mobilenet_v1"].file == "ssd_mobilenet_v1_12.onnx"
    assert MODELS["ultraface_rfb_320"].file == "version-RFB-320.onnx"
    assert MODELS["arcface_r100_int8"].file == "arcfaceresnet100-11-int8.onnx"
    assert all(spec.licence in ("MIT", "Apache-2.0") for spec in MODELS.values())


def test_ensure_model_downloads_once_and_reuses(tmp_path):
    client = _Client()
    path = ensure_model("ultraface_rfb_320", models_dir=tmp_path, client=client, write=lambda s: None)
    assert path == tmp_path / "version-RFB-320.onnx" and path.stat().st_size == 2_000_000
    ensure_model("ultraface_rfb_320", models_dir=tmp_path, client=client, write=lambda s: None)
    assert client.calls == [MODELS["ultraface_rfb_320"].url]


def test_ensure_model_rejects_a_truncated_download(tmp_path):
    client = _Client(content=b"tiny")
    with pytest.raises(RuntimeError, match="smaller than expected"):
        ensure_model("ultraface_rfb_320", models_dir=tmp_path, client=client, write=lambda s: None)
    assert not (tmp_path / "version-RFB-320.onnx").exists()


def test_ensure_model_unknown_name():
    with pytest.raises(KeyError):
        ensure_model("nope", models_dir="/nonexistent")


def test_session_options_fix_the_thread_count():
    pytest.importorskip("onnxruntime")
    opts = session_options(threads=3)
    assert opts.intra_op_num_threads == 3 and opts.inter_op_num_threads == 1
