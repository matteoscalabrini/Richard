"""The ONNX models perception runs, fetched on first use like Silero VAD.

Chosen 2026-09-09 for permissive licences and simple post-processing:
SSD-MobileNetV1 (ONNX model zoo, MIT) has NMS inside the graph; UltraFace RFB-320
(ONNX model zoo, MIT) is 1.3 MB; ArcFace ResNet100 int8 (ONNX model zoo, Apache-2.0)
gives 512-d embeddings. Thread counts are fixed explicitly: onnxruntime's default
affinity pinning fails noisily inside the LXC (pthread_setaffinity_np errors).
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from richard.realtime.vad import default_models_dir

_ZOO = "https://github.com/onnx/models/raw/main/validated/vision"


@dataclass(frozen=True)
class ModelSpec:
    name: str
    file: str
    url: str
    min_bytes: int
    licence: str


MODELS: dict[str, ModelSpec] = {
    "ssd_mobilenet_v1": ModelSpec(
        "ssd_mobilenet_v1", "ssd_mobilenet_v1_12.onnx",
        f"{_ZOO}/object_detection_segmentation/ssd-mobilenetv1/model/ssd_mobilenet_v1_12.onnx",
        25_000_000, "MIT"),
    "ultraface_rfb_320": ModelSpec(
        "ultraface_rfb_320", "version-RFB-320.onnx",
        f"{_ZOO}/body_analysis/ultraface/models/version-RFB-320.onnx",
        1_000_000, "MIT"),
    "arcface_r100_int8": ModelSpec(
        "arcface_r100_int8", "arcfaceresnet100-11-int8.onnx",
        f"{_ZOO}/body_analysis/arcface/model/arcfaceresnet100-11-int8.onnx",
        60_000_000, "Apache-2.0"),
}


def ensure_model(name: str, *, models_dir: Path | str | None = None, client=None, write=print) -> Path:
    spec = MODELS[name]
    models_dir = Path(models_dir) if models_dir is not None else default_models_dir()
    models_dir.mkdir(parents=True, exist_ok=True)
    target = models_dir / spec.file
    if target.exists() and target.stat().st_size >= spec.min_bytes:
        return target
    if client is None:
        import httpx

        client = httpx.Client(timeout=300.0, follow_redirects=True)
    write(f"Downloading {spec.file} ({spec.licence}) for perception...")
    try:
        resp = client.get(spec.url)
        resp.raise_for_status()
    except Exception as exc:
        raise RuntimeError(f"Failed to download {spec.file} from {spec.url}: {exc}") from exc
    if len(resp.content) < spec.min_bytes:
        raise RuntimeError(f"{spec.file}: download smaller than expected ({len(resp.content)} bytes)")
    tmp = target.with_suffix(".part")
    tmp.write_bytes(resp.content)
    tmp.replace(target)
    return target


def session_options(threads: int = 4):
    import onnxruntime

    opts = onnxruntime.SessionOptions()
    opts.intra_op_num_threads = max(1, int(threads))
    opts.inter_op_num_threads = 1
    opts.log_severity_level = 3  # errors only; the affinity warnings are noise
    return opts


def make_session(path, *, device: str = "cpu", threads: int = 4):
    import onnxruntime

    providers = ["CPUExecutionProvider"]
    if device == "cuda" and "CUDAExecutionProvider" in onnxruntime.get_available_providers():
        providers = ["CUDAExecutionProvider", "CPUExecutionProvider"]
    return onnxruntime.InferenceSession(str(path), sess_options=session_options(threads), providers=providers)
