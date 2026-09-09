"""Who is in the frame: UltraFace (MIT) finds faces, ArcFace int8 (Apache-2.0) embeds
them, a local sqlite gallery names them. Opt-in, local only, deletable.

No landmark alignment: the crop is the detector's box widened by 20 %. That costs
some embedding stability; the gate's "two agreeing matches" rule absorbs it. If
identification proves unreliable in the house, a 5-point landmark model is the
next step, not a bigger embedder.
"""
from __future__ import annotations

import sqlite3
import time
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from richard.perception import image
from richard.perception.detect import Detection
from richard.perception.models import ensure_model, make_session

FACE_INPUT = (320, 240)
EMBED_SIZE = 112


def nms(boxes: np.ndarray, scores: np.ndarray, iou: float) -> list[int]:
    """Greedy non-maximum suppression; boxes are x0, y0, x1, y1. Returns kept indices."""
    order = np.argsort(-scores)
    keep: list[int] = []
    while order.size:
        i = int(order[0])
        keep.append(i)
        if order.size == 1:
            break
        rest = order[1:]
        xx0 = np.maximum(boxes[i, 0], boxes[rest, 0])
        yy0 = np.maximum(boxes[i, 1], boxes[rest, 1])
        xx1 = np.minimum(boxes[i, 2], boxes[rest, 2])
        yy1 = np.minimum(boxes[i, 3], boxes[rest, 3])
        inter = np.clip(xx1 - xx0, 0, None) * np.clip(yy1 - yy0, 0, None)
        area_i = (boxes[i, 2] - boxes[i, 0]) * (boxes[i, 3] - boxes[i, 1])
        area_r = (boxes[rest, 2] - boxes[rest, 0]) * (boxes[rest, 3] - boxes[rest, 1])
        overlap = inter / np.maximum(area_i + area_r - inter, 1e-9)
        order = rest[overlap <= iou]
    return keep


class FaceDetector:
    def __init__(self, session, *, threshold: float = 0.7, iou: float = 0.4) -> None:
        self._session = session
        self.threshold = threshold
        self._iou = iou

    @classmethod
    def load(cls, *, device: str = "cpu", threads: int = 2, models_dir=None, write=print, **kwargs) -> "FaceDetector":
        path = ensure_model("ultraface_rfb_320", models_dir=models_dir, write=write)
        return cls(make_session(path, device=device, threads=threads), **kwargs)

    def detect(self, rgb: np.ndarray) -> list[Detection]:
        small = image.resize_exact(rgb, *FACE_INPUT).astype(np.float32)
        tensor = ((small - 127.0) / 128.0).transpose(2, 0, 1)[np.newaxis, ...].astype(np.float32)
        scores, boxes = self._session.run(["scores", "boxes"], {"input": np.ascontiguousarray(tensor)})
        probs = scores[0, :, 1]
        mask = probs >= self.threshold
        if not mask.any():
            return []
        cand_boxes = boxes[0][mask].astype(np.float32)
        cand_scores = probs[mask].astype(np.float32)
        keep = nms(cand_boxes, cand_scores, self._iou)
        return [Detection(box=tuple(round(float(v), 4) for v in cand_boxes[k]), score=round(float(cand_scores[k]), 4))
                for k in keep]


class FaceEmbedder:
    def __init__(self, session) -> None:
        self._session = session

    @classmethod
    def load(cls, *, device: str = "cpu", threads: int = 2, models_dir=None, write=print) -> "FaceEmbedder":
        path = ensure_model("arcface_r100_int8", models_dir=models_dir, write=write)
        return cls(make_session(path, device=device, threads=threads))

    def embed(self, rgb: np.ndarray, box: tuple[float, float, float, float]) -> np.ndarray:
        x0, y0, x1, y1 = box
        mx, my = (x1 - x0) * 0.2, (y1 - y0) * 0.2
        face = image.crop(rgb, (x0 - mx, y0 - my, x1 + mx, y1 + my))
        square = image.resize_exact(face, EMBED_SIZE, EMBED_SIZE).astype(np.float32)
        tensor = np.ascontiguousarray(square.transpose(2, 0, 1)[np.newaxis, ...], dtype=np.float32)
        (out,) = self._session.run(None, {"data": tensor})
        vec = np.asarray(out, dtype=np.float32).reshape(-1)
        return vec / max(float(np.linalg.norm(vec)), 1e-9)


class Gallery:
    """Enrolled people: name → embeddings. sqlite, check_same_thread off (pipeline threads)."""

    def __init__(self, path: Path | str) -> None:
        self._path = str(path)
        if self._path != ":memory:":
            Path(self._path).parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(self._path, check_same_thread=False)
        self._conn.execute("PRAGMA foreign_keys = ON")
        self._conn.execute("CREATE TABLE IF NOT EXISTS people (id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL UNIQUE, enrolled_at TEXT NOT NULL)")
        self._conn.execute("CREATE TABLE IF NOT EXISTS embeddings (id INTEGER PRIMARY KEY AUTOINCREMENT, person_id INTEGER NOT NULL REFERENCES people(id) ON DELETE CASCADE, vec BLOB NOT NULL)")
        self._conn.commit()

    def enrol(self, name: str, vectors: list[np.ndarray]) -> int:
        name = name.strip()
        if not name:
            raise ValueError("a name is required")
        row = self._conn.execute("SELECT id FROM people WHERE name = ?", (name,)).fetchone()
        if row is None:
            cur = self._conn.execute("INSERT INTO people (name, enrolled_at) VALUES (?, ?)",
                                     (name, time.strftime("%Y-%m-%dT%H:%M:%S")))
            person_id = int(cur.lastrowid)
        else:
            person_id = int(row[0])
        for vec in vectors:
            self._conn.execute("INSERT INTO embeddings (person_id, vec) VALUES (?, ?)",
                               (person_id, np.asarray(vec, dtype=np.float32).tobytes()))
        self._conn.commit()
        return int(self._conn.execute("SELECT COUNT(*) FROM embeddings WHERE person_id = ?", (person_id,)).fetchone()[0])

    def match(self, vector: np.ndarray, threshold: float) -> tuple[str, float] | None:
        best: tuple[str, float] | None = None
        probe = np.asarray(vector, dtype=np.float32)
        norm = float(np.linalg.norm(probe))
        if norm < 1e-9:
            return None
        probe = probe / norm
        for name, blob in self._conn.execute("SELECT p.name, e.vec FROM embeddings e JOIN people p ON p.id = e.person_id"):
            vec = np.frombuffer(blob, dtype=np.float32)
            score = float(np.dot(probe, vec) / max(float(np.linalg.norm(vec)), 1e-9))
            if score >= threshold and (best is None or score > best[1]):
                best = (name, score)
        return best

    def delete(self, name: str) -> bool:
        cur = self._conn.execute("DELETE FROM people WHERE name = ?", (name.strip(),))
        self._conn.commit()
        return cur.rowcount > 0

    def list(self) -> list[dict]:
        rows = self._conn.execute(
            "SELECT p.name, p.enrolled_at, COUNT(e.id) FROM people p LEFT JOIN embeddings e ON e.person_id = p.id "
            "GROUP BY p.id ORDER BY p.name").fetchall()
        return [{"name": r[0], "enrolled_at": r[1], "samples": int(r[2])} for r in rows]

    def close(self) -> None:
        self._conn.close()


@dataclass(frozen=True)
class FaceMatch:
    box: tuple[float, float, float, float]
    name: str | None
    score: float


class FaceIdentifier:
    def __init__(self, detector: FaceDetector, embedder: FaceEmbedder, gallery: Gallery, *, threshold: float = 0.45) -> None:
        self._detector = detector
        self._embedder = embedder
        self._gallery = gallery
        self.threshold = threshold

    def identify(self, rgb: np.ndarray) -> list[FaceMatch]:
        matches = []
        for face in self._detector.detect(rgb):
            vec = self._embedder.embed(rgb, face.box)
            hit = self._gallery.match(vec, self.threshold)
            matches.append(FaceMatch(box=face.box, name=hit[0] if hit else None, score=hit[1] if hit else 0.0))
        return matches

    def embed_box(self, rgb: np.ndarray, box: tuple[float, float, float, float]) -> np.ndarray:
        """The embedding of one detected face; enrolment uses it."""
        return self._embedder.embed(rgb, box)
