import numpy as np

from richard.perception.detect import Detection, PersonDetector


class _Named:
    def __init__(self, name):
        self.name = name


class FakeSession:
    """Mimics ssd_mobilenet_v1_12.onnx as downloaded: input `inputs`, outputs without `:0`,
    boxes as (top, left, bottom, right). `legacy=True` mimics a session that cannot report
    its names (the README's `image_tensor:0` vocabulary)."""

    def __init__(self, boxes, classes, scores, legacy=False):
        self.boxes, self.classes, self.scores = boxes, classes, scores
        self.inputs = []
        self._suffix = ":0" if legacy else ""
        self._input = "image_tensor:0" if legacy else "inputs"
        if not legacy:
            self.get_inputs = lambda: [_Named("inputs")]
            self.get_outputs = lambda: [_Named(n) for n in ("detection_boxes", "detection_classes", "detection_scores", "num_detections")]

    def run(self, output_names, feeds):
        assert list(feeds) == [self._input], list(feeds)
        self.inputs.append(feeds)
        n = len(self.boxes)
        out = {
            "detection_boxes" + self._suffix: np.array([self.boxes], dtype=np.float32).reshape(1, n, 4),
            "detection_classes" + self._suffix: np.array([self.classes], dtype=np.float32).reshape(1, n),
            "detection_scores" + self._suffix: np.array([self.scores], dtype=np.float32).reshape(1, n),
            "num_detections" + self._suffix: np.array([n], dtype=np.float32),
        }
        return [out[name] for name in output_names]


def test_detect_keeps_persons_above_threshold_in_xyxy_order():
    session = FakeSession(
        boxes=[[0.1, 0.2, 0.9, 0.6], [0.0, 0.0, 0.5, 0.5], [0.2, 0.2, 0.3, 0.3]],
        classes=[1, 3, 1], scores=[0.9, 0.95, 0.3],
    )
    det = PersonDetector(session, threshold=0.55)
    rgb = np.zeros((480, 640, 3), dtype=np.uint8)
    out = det.detect(rgb)
    assert out == [Detection(box=(0.2, 0.1, 0.6, 0.9), score=0.9)]


def test_detect_feeds_uint8_nhwc_at_work_width():
    session = FakeSession(boxes=[], classes=[], scores=[])
    det = PersonDetector(session, work_width=320)
    det.detect(np.zeros((900, 1600, 3), dtype=np.uint8))
    tensor = session.inputs[0]["inputs"]
    assert tensor.dtype == np.uint8 and tensor.shape == (1, 180, 320, 3)


def test_detect_falls_back_to_the_readme_names_when_the_session_cannot_report_them():
    session = FakeSession(boxes=[[0.1, 0.2, 0.9, 0.6]], classes=[1], scores=[0.9], legacy=True)
    out = PersonDetector(session).detect(np.zeros((100, 100, 3), dtype=np.uint8))
    assert out == [Detection(box=(0.2, 0.1, 0.6, 0.9), score=0.9)]
    assert list(session.inputs[0]) == ["image_tensor:0"]
