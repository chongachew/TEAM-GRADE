"""
Tests for the pure heuristic functions in processing/film_authenticity.py.
No Firestore/queue mocking - this module is intentionally decoupled from that
layer (see authenticity_check_stage.py / frame_extraction_stage_gpu.py for the
plumbing that calls into it).
"""

import json
from unittest.mock import MagicMock, patch

import cv2
import numpy as np

from processing.film_authenticity import analyze_file_integrity, analyze_frame_signals


def _ffprobe_result(returncode=0, payload=None):
    result = MagicMock()
    result.returncode = returncode
    result.stdout = json.dumps(payload or {})
    result.stderr = ""
    return result


class TestAnalyzeFileIntegrity:
    def test_missing_video_file_returns_unknown_confidence(self, tmp_path):
        result = analyze_file_integrity(tmp_path / "does-not-exist.mp4")

        assert result["flagged"] is False
        assert result["confidence"] == "unknown"
        assert "video_file_not_found" in result["reasons"]

    def test_ffprobe_unavailable_returns_unknown_confidence(self, tmp_path):
        video_path = tmp_path / "video.mp4"
        video_path.write_bytes(b"fake video bytes")

        with patch("processing.film_authenticity.subprocess.run", side_effect=FileNotFoundError("no ffprobe")):
            result = analyze_file_integrity(video_path)

        assert result["flagged"] is False
        assert result["confidence"] == "unknown"
        assert result["reasons"] == ["ffprobe_unavailable"]

    def test_clean_metadata_and_normal_keyframes_not_flagged(self, tmp_path):
        video_path = tmp_path / "video.mp4"
        video_path.write_bytes(b"fake video bytes")

        format_result = _ffprobe_result(payload={
            "format": {"tags": {"encoder": "Lavf60.16.100", "creation_time": "2026-01-01T00:00:00Z"}}
        })
        # 6 I-frames sampled over a 30s window -> 5s interval, well under the
        # default 12s threshold.
        frame_result = _ffprobe_result(payload={
            "frames": [{"pict_type": "I" if i % 5 == 0 else "P"} for i in range(30)]
        })

        with patch("processing.film_authenticity.subprocess.run", side_effect=[format_result, frame_result]):
            result = analyze_file_integrity(video_path)

        assert result["flagged"] is False
        assert result["confidence"] == "low"

    def test_missing_encoder_metadata_is_flagged(self, tmp_path):
        video_path = tmp_path / "video.mp4"
        video_path.write_bytes(b"fake video bytes")

        format_result = _ffprobe_result(payload={"format": {"tags": {}}})
        frame_result = _ffprobe_result(payload={
            "frames": [{"pict_type": "I" if i % 5 == 0 else "P"} for i in range(30)]
        })

        with patch("processing.film_authenticity.subprocess.run", side_effect=[format_result, frame_result]):
            result = analyze_file_integrity(video_path)

        assert result["flagged"] is True
        assert result["confidence"] == "medium"
        assert "missing_encoder_and_creation_time_metadata" in result["reasons"]

    def test_sparse_keyframe_interval_is_flagged(self, tmp_path):
        video_path = tmp_path / "video.mp4"
        video_path.write_bytes(b"fake video bytes")

        format_result = _ffprobe_result(payload={
            "format": {"tags": {"encoder": "Lavf60.16.100", "creation_time": "2026-01-01T00:00:00Z"}}
        })
        # Only 1 I-frame across a 30s window -> 30s interval, over the 12s default threshold.
        frame_result = _ffprobe_result(payload={
            "frames": [{"pict_type": "I"}] + [{"pict_type": "P"} for _ in range(29)]
        })

        with patch("processing.film_authenticity.subprocess.run", side_effect=[format_result, frame_result]):
            result = analyze_file_integrity(video_path)

        assert result["flagged"] is True
        assert any("sparse_keyframe_interval" in r for r in result["reasons"])

    def test_never_raises_on_unexpected_error(self, tmp_path):
        video_path = tmp_path / "video.mp4"
        video_path.write_bytes(b"fake video bytes")

        with patch("processing.film_authenticity.subprocess.run", side_effect=RuntimeError("boom")):
            result = analyze_file_integrity(video_path)

        assert result["confidence"] == "unknown"
        assert result["flagged"] is False


class TestAnalyzeFrameSignals:
    def _write_solid_frame(self, path, color):
        img = np.full((64, 64, 3), color, dtype=np.uint8)
        cv2.imwrite(str(path), img)

    def _write_frame_with_rect(self, path, rect_x):
        img = np.zeros((64, 64, 3), dtype=np.uint8)
        cv2.rectangle(img, (rect_x, 10), (rect_x + 20, 50), (255, 255, 255), -1)
        cv2.imwrite(str(path), img)

    def test_fewer_than_two_readable_frames_returns_unknown(self, tmp_path):
        result = analyze_frame_signals([tmp_path / "missing.jpg"])

        assert result["flagged"] is False
        assert result["confidence"] == "unknown"
        assert result["reasons"] == ["not_enough_readable_frames"]

    def test_identical_static_frames_are_flagged(self, tmp_path):
        """Identical frames trivially have zero edge-density variance and zero
        motion - both deterministically below any positive threshold."""
        paths = []
        for i in range(4):
            p = tmp_path / f"frame_{i}.jpg"
            self._write_solid_frame(p, 128)
            paths.append(p)

        result = analyze_frame_signals(paths)

        assert result["flagged"] is True
        assert result["confidence"] == "medium"
        assert any("suspiciously_static" in r for r in result["reasons"])

    def test_frames_with_real_motion_are_not_flagged_as_static(self, tmp_path):
        paths = []
        for i, rect_x in enumerate([0, 15, 30, 45]):
            p = tmp_path / f"frame_{i}.jpg"
            self._write_frame_with_rect(p, rect_x)
            paths.append(p)

        result = analyze_frame_signals(paths)

        assert not any("suspiciously_static" in r for r in result["reasons"])
        assert result["raw"]["mean_motion_diff"] > 1.0

    def test_never_raises_on_unreadable_frames_mixed_in(self, tmp_path):
        paths = [tmp_path / "nonexistent.jpg"]
        for i in range(3):
            p = tmp_path / f"frame_{i}.jpg"
            self._write_frame_with_rect(p, i * 10)
            paths.append(p)

        result = analyze_frame_signals(paths)

        # 3 readable frames is enough to proceed past the "not enough" guard.
        assert result["confidence"] in ("low", "medium")
