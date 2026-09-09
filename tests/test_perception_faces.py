import numpy as np
import pytest

pytest.importorskip("PIL")

from richard.perception import image  # noqa: E402
from richard.perception.detect import Detection  # noqa: E402
from richard.perception.faces import FaceDetector, FaceEmbedder, FaceIdentifier, FaceMatch, Gallery, nms  # noqa: E402


class FakeFaceSession:
    def __init__(self, faces):  # faces: list of (x0, y0, x1, y1, prob)
        self.faces = faces
        self.feeds = []

    def run(self, output_names, feeds):
        self.feeds.append(feeds)
        n = 4420
        scores = np.zeros((1, n, 2), dtype=np.float32)
        boxes = np.zeros((1, n, 4), dtype=np.float32)
        scores[0, :, 0] = 1.0
        for i, (x0, y0, x1, y1, p) in enumerate(self.faces):
            scores[0, i] = (1 - p, p)
            boxes[0, i] = (x0, y0, x1, y1)
        return [scores, boxes]


class FakeEmbedSession:
    def __init__(self, vec):
        self.vec = np.asarray(vec, dtype=np.float32)
        self.feeds = []

    def run(self, output_names, feeds):
        self.feeds.append(feeds)
        return [self.vec[np.newaxis, :]]


def test_nms_suppresses_overlapping_boxes():
    boxes = np.array([[0, 0, 10, 10], [1, 1, 11, 11], [50, 50, 60, 60]], dtype=np.float32)
    scores = np.array([0.9, 0.8, 0.7], dtype=np.float32)
    assert nms(boxes, scores, iou=0.4) == [0, 2]


def test_face_detector_preprocesses_and_filters():
    session = FakeFaceSession([(0.1, 0.1, 0.3, 0.4, 0.95), (0.11, 0.1, 0.31, 0.4, 0.9), (0.6, 0.6, 0.7, 0.8, 0.2)])
    det = FaceDetector(session, threshold=0.7, iou=0.4)
    out = det.detect(np.full((480, 640, 3), 127, dtype=np.uint8))
    assert out == [Detection(box=(0.1, 0.1, 0.3, 0.4), score=0.95)]
    tensor = session.feeds[0]["input"]
    assert tensor.shape == (1, 3, 240, 320) and tensor.dtype == np.float32
    assert abs(float(tensor.mean())) < 0.01  # (127 - 127) / 128


def test_face_embedder_crops_with_margin_and_normalises():
    session = FakeEmbedSession([3.0, 4.0] + [0.0] * 510)
    emb = FaceEmbedder(session)
    vec = emb.embed(np.zeros((200, 200, 3), dtype=np.uint8), (0.4, 0.4, 0.6, 0.6))
    assert vec.shape == (512,) and abs(float(np.linalg.norm(vec)) - 1.0) < 1e-5
    tensor = session.feeds[0]["data"]
    assert tensor.shape == (1, 3, 112, 112) and tensor.dtype == np.float32


def test_gallery_enrol_match_delete(tmp_path):
    g = Gallery(tmp_path / "faces.db")
    a = np.zeros(512, dtype=np.float32); a[0] = 1.0
    b = np.zeros(512, dtype=np.float32); b[1] = 1.0
    assert g.enrol("matteo", [a]) == 1
    assert g.enrol("guest", [b, b]) == 2
    assert [(p["name"], p["samples"]) for p in g.list()] == [("guest", 2), ("matteo", 1)]
    probe = np.zeros(512, dtype=np.float32); probe[0] = 0.9; probe[1] = 0.1
    name, score = g.match(probe, threshold=0.45)
    assert name == "matteo" and score > 0.9
    assert g.match(np.zeros(512, dtype=np.float32), threshold=0.45) is None
    assert g.delete("matteo") is True and g.delete("matteo") is False
    assert g.match(probe, threshold=0.45) is None
    g.close()


def test_identifier_names_known_faces_and_flags_unknown(tmp_path):
    g = Gallery(tmp_path / "faces.db")
    known = np.zeros(512, dtype=np.float32); known[2] = 1.0
    g.enrol("matteo", [known])
    detector = FaceDetector(FakeFaceSession([(0.1, 0.1, 0.3, 0.4, 0.95)]))
    ident = FaceIdentifier(detector, FaceEmbedder(FakeEmbedSession(known)), g, threshold=0.45)
    assert ident.identify(np.zeros((100, 100, 3), dtype=np.uint8)) == [FaceMatch(box=(0.1, 0.1, 0.3, 0.4), name="matteo", score=pytest.approx(1.0))]
    other = np.zeros(512, dtype=np.float32); other[5] = 1.0
    ident2 = FaceIdentifier(detector, FaceEmbedder(FakeEmbedSession(other)), g, threshold=0.45)
    assert ident2.identify(np.zeros((100, 100, 3), dtype=np.uint8))[0].name is None
