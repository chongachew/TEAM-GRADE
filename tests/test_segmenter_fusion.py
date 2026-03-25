"""
Unit tests for segmenter_fusion.SegmenterFusion.

All tests use synthetic signal data — no video files, no audio, no GPU.
"""

from __future__ import annotations

import unittest


class TestSegmenterFusion(unittest.TestCase):

    def _make_fusion(self, **kwargs):
        from preprocessor.segmenter_fusion import SegmenterFusion
        defaults = dict(
            pad_s=1.0,
            min_duration_s=3.0,
            max_duration_s=60.0,
            min_confidence=0.30,
            w_whistle=0.35,
            w_motion=0.25,
            w_scene=0.20,
            w_field=0.20,
        )
        defaults.update(kwargs)
        return SegmenterFusion(**defaults)

    # ------------------------------------------------------------------
    # _build_candidates
    # ------------------------------------------------------------------

    def test_no_signals_returns_empty(self):
        fusion = self._make_fusion()
        result = fusion.fuse([], [], [], [])
        self.assertEqual(result, [])

    def test_single_whistle_creates_candidate(self):
        """A single whistle event with a motion burst should produce a candidate."""
        fusion = self._make_fusion(min_confidence=0.0)
        whistle = [{"start_time": 10.0, "end_time": 10.5,
                    "confidence": 0.9, "whistle_confidence": 0.9}]
        motion = [{"start_time": 8.0, "end_time": 20.0, "motion_score": 30.0}]
        field = [{"time": float(t), "field_presence": 0.7} for t in range(30)]
        result = fusion.fuse(whistle, motion, [], field)
        self.assertGreater(len(result), 0)

    def test_candidate_schema(self):
        """Each candidate must expose all required fields."""
        fusion = self._make_fusion(min_confidence=0.0)
        whistle = [{"start_time": 5.0, "end_time": 6.0,
                    "confidence": 0.8, "whistle_confidence": 0.8}]
        motion = [{"start_time": 4.0, "end_time": 18.0, "motion_score": 40.0}]
        field = [{"time": float(t), "field_presence": 0.6} for t in range(25)]
        result = fusion.fuse(whistle, motion, [], field)
        required = {"start_time", "end_time", "duration",
                    "whistle_confidence", "motion_score",
                    "scene_score", "field_presence", "fusion_confidence"}
        for cand in result:
            d = cand.to_dict()
            for key in required:
                self.assertIn(key, d, f"Missing key: {key}")

    def test_fusion_confidence_in_range(self):
        """fusion_confidence must be in [0, 1]."""
        fusion = self._make_fusion(min_confidence=0.0)
        whistle = [{"start_time": 2.0, "end_time": 2.5,
                    "confidence": 1.0, "whistle_confidence": 1.0}]
        motion = [{"start_time": 0.0, "end_time": 10.0, "motion_score": 50.0}]
        field = [{"time": float(t), "field_presence": 0.9} for t in range(15)]
        for cand in fusion.fuse(whistle, motion, [], field):
            self.assertGreaterEqual(cand.fusion_confidence, 0.0)
            self.assertLessEqual(cand.fusion_confidence, 1.0)

    def test_min_confidence_filter(self):
        """Candidates below min_confidence must be discarded."""
        fusion = self._make_fusion(min_confidence=0.99)
        # Low-confidence inputs
        whistle = [{"start_time": 5.0, "end_time": 5.5,
                    "confidence": 0.1, "whistle_confidence": 0.1}]
        motion = [{"start_time": 4.0, "end_time": 7.0, "motion_score": 1.0}]
        field = [{"time": float(t), "field_presence": 0.0} for t in range(15)]
        result = fusion.fuse(whistle, motion, [], field)
        self.assertEqual(result, [])

    def test_segment_duration_clipped(self):
        """Segments longer than max_duration_s must be clipped."""
        fusion = self._make_fusion(min_confidence=0.0, max_duration_s=10.0)
        motion = [{"start_time": 0.0, "end_time": 300.0, "motion_score": 80.0}]
        field = [{"time": float(t), "field_presence": 0.8} for t in range(310)]
        result = fusion.fuse([], motion, [], field)
        for cand in result:
            self.assertLessEqual(cand.duration, 10.0 + 1e-6)

    def test_segments_sorted_by_start(self):
        """Candidates must be sorted by start_time."""
        fusion = self._make_fusion(min_confidence=0.0)
        motion = [
            {"start_time": 50.0, "end_time": 55.0, "motion_score": 50.0},
            {"start_time": 10.0, "end_time": 15.0, "motion_score": 50.0},
            {"start_time": 30.0, "end_time": 35.0, "motion_score": 50.0},
        ]
        field = [{"time": float(t), "field_presence": 0.7} for t in range(60)]
        result = fusion.fuse([], motion, [], field)
        times = [c.start_time for c in result]
        self.assertEqual(times, sorted(times))

    def test_overlapping_intervals_deduped(self):
        """Overlapping candidates should be collapsed to the higher-scoring one."""
        fusion = self._make_fusion(min_confidence=0.0)
        # Two overlapping motion bursts close in time
        motion = [
            {"start_time": 10.0, "end_time": 20.0, "motion_score": 30.0},
            {"start_time": 12.0, "end_time": 22.0, "motion_score": 30.0},
        ]
        field = [{"time": float(t), "field_presence": 0.5} for t in range(30)]
        result = fusion.fuse([], motion, [], field)
        # After dedup, start times should be non-overlapping
        for i in range(len(result) - 1):
            self.assertGreaterEqual(result[i + 1].start_time, result[i].end_time)

    def test_scene_boundary_boosts_score(self):
        """Adding a scene boundary inside a window should increase fusion score."""
        fusion = self._make_fusion(min_confidence=0.0)
        motion = [{"start_time": 5.0, "end_time": 15.0, "motion_score": 30.0}]
        field = [{"time": float(t), "field_presence": 0.5} for t in range(20)]
        without_scene = fusion.fuse([], motion, [], field)
        scene = [{"start_time": 7.0, "end_time": 9.0}]
        with_scene = fusion.fuse([], motion, scene, field)

        if without_scene and with_scene:
            self.assertGreaterEqual(
                with_scene[0].fusion_confidence,
                without_scene[0].fusion_confidence,
            )

    # ------------------------------------------------------------------
    # _overlaps helper
    # ------------------------------------------------------------------

    def test_overlaps_true(self):
        from preprocessor.segmenter_fusion import _overlaps
        self.assertTrue(_overlaps(0.0, 5.0, 3.0, 8.0))

    def test_overlaps_false(self):
        from preprocessor.segmenter_fusion import _overlaps
        self.assertFalse(_overlaps(0.0, 3.0, 5.0, 8.0))

    def test_overlaps_touching(self):
        from preprocessor.segmenter_fusion import _overlaps
        # [0,3) and [3,6) share no interior — should NOT overlap
        self.assertFalse(_overlaps(0.0, 3.0, 3.0, 6.0))


if __name__ == "__main__":
    unittest.main()
