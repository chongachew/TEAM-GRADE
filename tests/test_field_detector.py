"""
Unit tests for field_detector.FieldDetector and green_coverage.

All tests use synthetic numpy frame arrays — no video files, no GPU required.
"""

from __future__ import annotations

import unittest

import numpy as np

try:
    import cv2
    HAS_CV2 = True
except ImportError:
    HAS_CV2 = False


def _make_green_frame(h: int = 480, w: int = 854,
                      coverage: float = 1.0) -> np.ndarray:
    """Return a BGR frame where *coverage* fraction is grass-green pixels."""
    # HSV (60, 200, 150) → BGR ≈ (50, 150, 50) — well within FIELD_HSV range
    green_bgr = (50, 150, 50)
    non_green_bgr = (200, 50, 50)  # red-ish, definitely not grass

    frame = np.zeros((h, w, 3), dtype=np.uint8)
    green_cols = int(w * coverage)
    frame[:, :green_cols] = green_bgr
    frame[:, green_cols:] = non_green_bgr
    return frame


def _make_non_green_frame(h: int = 480, w: int = 854) -> np.ndarray:
    """Return a frame with no grass-green pixels (e.g., crowd/sky)."""
    frame = np.zeros((h, w, 3), dtype=np.uint8)
    frame[:] = (200, 50, 50)  # reddish
    return frame


@unittest.skipUnless(HAS_CV2, "opencv-python required")
class TestGreenCoverage(unittest.TestCase):
    """Tests for the standalone green_coverage() function."""

    def test_full_green_frame(self):
        from preprocessor.field_detector import green_coverage
        frame = _make_green_frame(coverage=1.0)
        cov = green_coverage(frame)
        self.assertGreater(cov, 0.8)

    def test_no_green_frame(self):
        from preprocessor.field_detector import green_coverage
        frame = _make_non_green_frame()
        cov = green_coverage(frame)
        self.assertLess(cov, 0.05)

    def test_partial_green_frame(self):
        from preprocessor.field_detector import green_coverage
        frame = _make_green_frame(coverage=0.5)
        cov = green_coverage(frame)
        # Coverage should be roughly 0.5 (allow ±0.15 for HSV boundary effects)
        self.assertGreater(cov, 0.30)
        self.assertLess(cov, 0.70)

    def test_returns_float_in_range(self):
        from preprocessor.field_detector import green_coverage
        frame = _make_green_frame(coverage=0.75)
        cov = green_coverage(frame)
        self.assertIsInstance(cov, float)
        self.assertGreaterEqual(cov, 0.0)
        self.assertLessEqual(cov, 1.0)


@unittest.skipUnless(HAS_CV2, "opencv-python required")
class TestFieldDetector(unittest.TestCase):
    """Tests for FieldDetector.analyze_frames()."""

    def setUp(self):
        from preprocessor.field_detector import FieldDetector
        self.detector = FieldDetector(min_coverage=0.25)

    def test_scores_all_frames(self):
        """analyze_frames returns one entry per input frame."""
        frames = [_make_green_frame() for _ in range(5)]
        scores = self.detector.analyze_frames(frames)
        self.assertEqual(len(scores), 5)

    def test_score_schema(self):
        """Each score dict must have 'time' and 'field_presence' keys."""
        frames = [_make_green_frame(), _make_non_green_frame()]
        scores = self.detector.analyze_frames(frames)
        for s in scores:
            self.assertIn("time", s)
            self.assertIn("field_presence", s)

    def test_time_increments(self):
        """time values should be monotonically increasing."""
        frames = [_make_green_frame() for _ in range(4)]
        scores = self.detector.analyze_frames(frames)
        times = [s["time"] for s in scores]
        self.assertEqual(times, sorted(times))

    def test_mean_presence(self):
        """mean_presence should average field_presence in the time window."""
        frames = [_make_green_frame(coverage=1.0)] * 5 + \
                 [_make_non_green_frame()] * 5
        scores = self.detector.analyze_frames(frames)
        mean_green = self.detector.mean_presence(scores, 0.0, 4.0)
        mean_non_green = self.detector.mean_presence(scores, 5.0, 9.0)
        self.assertGreater(mean_green, mean_non_green)

    def test_is_field_present_true(self):
        """is_field_present returns True when coverage exceeds threshold."""
        frames = [_make_green_frame(coverage=1.0)] * 5
        scores = self.detector.analyze_frames(frames)
        self.assertTrue(self.detector.is_field_present(scores, 0.0, 4.0))

    def test_is_field_present_false(self):
        """is_field_present returns False for non-green frames."""
        frames = [_make_non_green_frame() for _ in range(5)]
        scores = self.detector.analyze_frames(frames)
        self.assertFalse(self.detector.is_field_present(scores, 0.0, 4.0))

    def test_empty_window_returns_false(self):
        """is_field_present returns False when window has no scores."""
        scores = self.detector.analyze_frames([_make_green_frame()])
        self.assertFalse(self.detector.is_field_present(scores, 100.0, 200.0))


if __name__ == "__main__":
    unittest.main()
