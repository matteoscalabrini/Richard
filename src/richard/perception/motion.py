"""Cheapest stage first: frame differencing on a 160x90 grey image.

activity = fraction of pixels whose grey level moved by more than 25 since the
previous frame. A scene change is a large difference from a slowly adapting
background that persists (lights on, a moved camera), reported once per episode.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np

PIXEL_DELTA = 25.0


@dataclass(frozen=True)
class MotionReading:
    activity: float
    scene_changed: bool


class MotionDetector:
    def __init__(self, *, threshold: float = 0.06, scene_change_ratio: float = 0.5,
                 scene_change_hold_s: float = 3.0, background_alpha: float = 0.02) -> None:
        self.threshold = threshold
        self._scene_ratio = scene_change_ratio
        self._hold_s = scene_change_hold_s
        self._alpha = background_alpha
        self._previous: np.ndarray | None = None
        self._background: np.ndarray | None = None
        self._changed_since: float | None = None
        self._reported = False

    def feed(self, grey: np.ndarray, ts: float) -> MotionReading:
        grey = grey.astype(np.float32, copy=False)
        if self._previous is None:
            self._previous = grey
            self._background = grey.copy()
            return MotionReading(activity=0.0, scene_changed=False)
        activity = float(np.mean(np.abs(grey - self._previous) > PIXEL_DELTA))
        self._previous = grey
        away = float(np.mean(np.abs(grey - self._background) > PIXEL_DELTA))
        scene_changed = False
        if away >= self._scene_ratio:
            if self._changed_since is None:
                self._changed_since = ts
            elif not self._reported and ts - self._changed_since > self._hold_s:
                scene_changed = True
                self._reported = True
        else:
            self._changed_since = None
            self._reported = False
        # The background follows the scene slowly, so a new arrangement becomes normal.
        self._background = (1.0 - self._alpha) * self._background + self._alpha * grey
        return MotionReading(activity=activity, scene_changed=scene_changed)


class StillnessTracker:
    """Minutes without movement; reports once when the still period passes the bar."""

    def __init__(self, *, still_after_s: float = 600.0) -> None:
        self._after = still_after_s
        self.still_since: float | None = None
        self._reported = False

    def feed(self, activity: float, threshold: float, ts: float) -> float | None:
        if activity > threshold:
            self.still_since = None
            self._reported = False
            return None
        if self.still_since is None:
            self.still_since = ts
            return None
        if not self._reported and ts - self.still_since >= self._after:
            self._reported = True
            return round((ts - self.still_since) / 60.0, 1)
        return None
