import numpy as np

from richard.perception.motion import MotionDetector, MotionReading, StillnessTracker


def _grey(value=50.0):
    return np.full((90, 160), value, dtype=np.float32)


def test_first_frame_has_no_activity():
    det = MotionDetector()
    reading = det.feed(_grey(), ts=0.0)
    assert reading == MotionReading(activity=0.0, scene_changed=False)


def test_local_change_yields_proportional_activity():
    det = MotionDetector()
    det.feed(_grey(), ts=0.0)
    moved = _grey()
    moved[:, :16] = 200.0  # 10 % of the pixels change a lot
    reading = det.feed(moved, ts=0.5)
    assert 0.08 < reading.activity < 0.12
    assert reading.scene_changed is False


def test_scene_change_needs_a_large_persistent_difference_from_the_background():
    det = MotionDetector(scene_change_hold_s=3.0)
    for i in range(10):
        det.feed(_grey(50.0), ts=i * 0.5)  # background settles at 50
    bright = _grey(200.0)  # the lights went on
    assert det.feed(bright, ts=5.0).scene_changed is False   # not yet persistent
    assert det.feed(bright, ts=7.0).scene_changed is False
    assert det.feed(bright, ts=8.1).scene_changed is True    # held for > 3 s
    assert det.feed(bright, ts=8.6).scene_changed is False   # reported once


def test_stillness_tracker_fires_once_per_still_period():
    tracker = StillnessTracker(still_after_s=60.0)
    assert tracker.feed(0.0, threshold=0.06, ts=0.0) is None
    assert tracker.still_since == 0.0
    assert tracker.feed(0.0, threshold=0.06, ts=30.0) is None
    assert tracker.feed(0.0, threshold=0.06, ts=61.0) == 1.0  # ~1 minute still
    assert tracker.feed(0.0, threshold=0.06, ts=120.0) is None  # already reported
    assert tracker.feed(0.5, threshold=0.06, ts=121.0) is None  # movement resets
    assert tracker.still_since is None
    assert tracker.feed(0.0, threshold=0.06, ts=122.0) is None
    assert tracker.still_since == 122.0
