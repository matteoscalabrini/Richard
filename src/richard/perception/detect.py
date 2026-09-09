"""People in the frame: SSD-MobileNetV1 from the ONNX model zoo (MIT).

The graph does its own NMS and takes any image size (it resizes to 300x300
inside), so the work here is a resize to 320 px wide, one run, and a filter on
class 1 (COCO person) above the threshold. ~40 ms on the CT's CPU cores.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from richard.perception import image
from richard.perception.models import ensure_model, make_session

PERSON_CLASS = 1
# The model zoo README documents the opset-10 names (`image_tensor:0` ...); the opset-12
# file actually says `inputs`, `detection_boxes`, ... Names are read from the graph when
# the session can tell us, and these are only the fallback for sessions that cannot.
FALLBACK_INPUT = "image_tensor:0"
FALLBACK_OUTPUTS = ["detection_boxes:0", "detection_classes:0", "detection_scores:0", "num_detections:0"]
OUTPUT_KEYS = ["detection_boxes", "detection_classes", "detection_scores", "num_detections"]


@dataclass(frozen=True)
class Detection:
    box: tuple[float, float, float, float]  # normalised x0, y0, x1, y1
    score: float


def resolve_io(session, fallback_input: str, output_keys: list[str], fallback_outputs: list[str]) -> tuple[str, list[str]]:
    """Input name and output names in the order of `output_keys`, read from the graph
    (each key must be a substring of exactly one output name) or the fallbacks."""
    get_inputs = getattr(session, "get_inputs", None)
    get_outputs = getattr(session, "get_outputs", None)
    if get_inputs is None or get_outputs is None:
        return fallback_input, list(fallback_outputs)
    input_name = get_inputs()[0].name
    names = [o.name for o in get_outputs()]
    outputs = []
    for key, fallback in zip(output_keys, fallback_outputs):
        hits = [n for n in names if key in n]
        if len(hits) != 1:
            raise ValueError(f"model output for {key!r} not found among {names}")
        outputs.append(hits[0])
    return input_name, outputs


class PersonDetector:
    def __init__(self, session, *, threshold: float = 0.55, work_width: int = 320) -> None:
        self._session = session
        self.threshold = threshold
        self._work_width = work_width
        self._input, self._outputs = resolve_io(session, FALLBACK_INPUT, OUTPUT_KEYS, FALLBACK_OUTPUTS)

    @classmethod
    def load(cls, *, device: str = "cpu", threads: int = 4, models_dir=None, write=print, **kwargs) -> "PersonDetector":
        path = ensure_model("ssd_mobilenet_v1", models_dir=models_dir, write=write)
        return cls(make_session(path, device=device, threads=threads), **kwargs)

    def detect(self, rgb: np.ndarray) -> list[Detection]:
        small = image.resize_long_edge(rgb, self._work_width) if rgb.shape[1] > self._work_width else rgb
        tensor = np.ascontiguousarray(small, dtype=np.uint8)[np.newaxis, ...]
        boxes, classes, scores, count = self._session.run(self._outputs, {self._input: tensor})
        n = int(count[0])
        found = []
        for i in range(n):
            if int(round(float(classes[0][i]))) != PERSON_CLASS or float(scores[0][i]) < self.threshold:
                continue
            top, left, bottom, right = (round(float(v), 4) for v in boxes[0][i])
            found.append(Detection(box=(left, top, right, bottom), score=round(float(scores[0][i]), 4)))
        return found
