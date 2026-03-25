"""
Unit tests for motion_analyzer.MotionAnalyzer.

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


def _blank_frame(h: int = 480, w: int = 854,
                 color=(0, 0, 0)) -> np.ndarray:
    """Return a solid-colour BGR frame (black by default = gray 0)."""
    frame = np.zeros((h, w, 3), dtype=np.uint8)
    frame[:] = color
    return frame


def _noisy_frame(h: int = 480, w: int = 854,
                 noise_level: int = 255) -> np.ndarray:
    """Return a frame with random per-pixel noise (simulates motion).

    Uses high-intensity noise (default 0–255) so the mean absolute diff
    versus a black blank frame is ≈ 127, well above any practical threshold.
    """
    return np.random.randint(128, noise_level, (h, w, 3), dtype=np.uint8)


@unittest.skipUnless(HAS_CV2, "opencv-python required")
class TestMotionAnalyzer(unittest.TestCase):

    def setUp(self):
        from preprocessor.motion_analyzer import MotionAnalyzer
        self.analyzer = MotionAnalyzer(
            diff_threshold=20,
            burst_min_frames=2,
            burst_gap_frames=3,
            sample_fps=4,
        )

    # ------------------------------------------------------------------
    # analyze_frames — static frame lists
    # ------------------------------------------------------------------

    def test_no_motion_returns_empty(self):
        """Identical frames → no motion bursts."""
        frames = [_blank_frame() for _ in range(20)]
        bursts = self.analyzer.analyze_frames(frames)
        self.assertEqual(bursts, [])

    def test_detects_motion_burst(self):
        """Alternating blank / noisy frames → at least one burst detected."""
        frames = (
            [_blank_frame() for _ in range(5)]
            + [_noisy_frame() for _ in range(8)]
            + [_blank_frame() for _ in range(5)]
        )

        bursts = self.analyzer.analyze_frames(frames)
        self.assertGreater(len(bursts), 0)

    def test_burst_schema(self):
        """Each burst must have start_time, end_time, motion_score keys."""
        frames = (
            [_blank_frame() for _ in range(3)]
            + [_noisy_frame() for _ in range(6)]
            + [_blank_frame() for _ in range(3)]
        )
        bursts = self.analyzer.analyze_frames(frames)
        for b in bursts:
            self.assertIn("start_time", b)
            self.assertIn("end_time", b)
            self.assertIn("motion_score", b)
            self.assertGreater(b["end_time"], b["start_time"])
            self.assertGreaterEqual(b["motion_score"], 0.0)

    def test_burst_min_frames_respected(self):
        """Single noisy frame (< burst_min_frames=2) should not create a burst."""
        analyzer_strict = __import__(
            "preprocessor.motion_analyzer", fromlist=["MotionAnalyzer"]
        ).MotionAnalyzer(
            diff_threshold=10,
            burst_min_frames=5,  # require 5 consecutive high-motion frames
            burst_gap_frames=2,
            sample_fps=4,
        )
        # Only 3 noisy frames — below min_frames=5
        frames = (
            [_blank_frame() for _ in range(3)]
            + [_noisy_frame() for _ in range(3)]
            + [_blank_frame() for _ in range(3)]
        )
        bursts = analyzer_strict.analyze_frames(frames)
        self.assertEqual(len(bursts), 0)

    def test_timing_accuracy(self):
        """Burst start_time should reflect the frame index / sample_fps."""
        # 4 static + 8 noisy at fps=4 → burst starts at ~1.0 s
        frames = (
            [_blank_frame() for _ in range(4)]
            + [_noisy_frame() for _ in range(8)]
        )
        bursts = self.analyzer.analyze_frames(frames)
        self.assertGreater(len(bursts), 0)
        self.assertGreaterEqual(bursts[0]["start_time"], 0.5)

    def test_too_few_frames_returns_empty(self):
        """A single frame cannot produce a diff."""
        bursts = self.analyzer.analyze_frames([_blank_frame()])
        self.assertEqual(bursts, [])

    def test_gap_bridging(self):
        """Short gaps between bursts should be merged."""
        # 4 noisy, 2 static (gap < burst_gap_frames=3), 4 noisy
        frames = (
            [_noisy_frame() for _ in range(4)]
            + [_blank_frame() for _ in range(2)]
            + [_noisy_frame() for _ in range(4)]
        )
        bursts = self.analyzer.analyze_frames(frames)
        # After bridging the 2-frame gap, we expect one contiguous burst
        self.assertEqual(len(bursts), 1)


if __name__ == "__main__":
    unittest.main()
