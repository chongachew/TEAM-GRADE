"""
Tests for the authenticity vision/motion pass hooked into
frame_extraction_stage_gpu.py (rides along with frame_extraction, per
docs/team-grade-integration-blueprint.md's "AI verification" section). Only
covers that hook - not general frame_extraction behavior, which has no
existing test coverage to extend (frame_extraction_stage_gpu.py had zero
direct tests before this).
"""

from pathlib import Path
from unittest.mock import MagicMock, patch

import cv2
import numpy as np

from config import settings
from ingest.stages import frame_extraction_stage_gpu


class FakeDoc:
    def __init__(self, data, exists=True):
        self._data = data
        self.exists = exists

    def to_dict(self):
        return self._data


def _make_extractor_mock(frame_paths):
    frames_metadata = [
        {"frame_index": i, "timestamp_seconds": i / 15.0, "path": str(p), "width": 64, "height": 64}
        for i, p in enumerate(frame_paths)
    ]
    extractor = MagicMock()
    extractor.device_type = "CPU (FFmpeg)"
    extractor.extract_frames.return_value = (len(frame_paths), frames_metadata)
    return extractor


def _write_frame(path, rect_x):
    img = np.zeros((64, 64, 3), dtype=np.uint8)
    cv2.rectangle(img, (rect_x, 10), (rect_x + 20, 50), (255, 255, 255), -1)
    cv2.imwrite(str(path), img)


def _setup_common(tmp_path, monkeypatch):
    video_path = tmp_path / "video.mp4"
    video_path.write_bytes(b"fake video bytes")

    frames_dir = tmp_path / "frames"
    frames_dir.mkdir()
    frame_paths = []
    for i, rect_x in enumerate([0, 15, 30, 45]):
        p = frames_dir / f"frame_{i:06d}.jpg"
        _write_frame(p, rect_x)
        frame_paths.append(p)

    monkeypatch.setattr(settings, "get_frames_dir", lambda video_id: frames_dir)

    mock_firestore = MagicMock()
    video_doc_mock = MagicMock()
    video_doc_mock.get.return_value = FakeDoc({"download_path": str(video_path)})
    mock_firestore.db.collection.return_value.document.return_value = video_doc_mock

    mock_queue = MagicMock()
    mock_queue.enqueue_video.return_value = True

    return video_doc_mock, mock_firestore, mock_queue, frame_paths


def test_authenticity_pass_skipped_when_disabled(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "AUTHENTICITY_CHECK_ENABLED", False)
    video_doc_mock, mock_firestore, mock_queue, frame_paths = _setup_common(tmp_path, monkeypatch)
    extractor = _make_extractor_mock(frame_paths)

    with patch.object(frame_extraction_stage_gpu.FrameExtractorFactory, "create", return_value=extractor), \
         patch.object(frame_extraction_stage_gpu, "write_frames_batch", return_value=len(frame_paths)):
        success, error = frame_extraction_stage_gpu.run_frame_extraction_stage(
            mock_firestore, "dQw4w9WgXcQ", mock_queue
        )

    assert success is True, error
    update_call = video_doc_mock.update.call_args[0][0]
    assert "stages.frame_extraction.authenticity_vision_motion" not in update_call
    assert "authenticity_signals.vision_motion" not in update_call


def test_authenticity_pass_runs_and_writes_result_when_enabled(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "AUTHENTICITY_CHECK_ENABLED", True)
    monkeypatch.setattr(settings, "AUTHENTICITY_SAMPLE_FRAME_COUNT", 4)
    video_doc_mock, mock_firestore, mock_queue, frame_paths = _setup_common(tmp_path, monkeypatch)
    extractor = _make_extractor_mock(frame_paths)

    with patch.object(frame_extraction_stage_gpu.FrameExtractorFactory, "create", return_value=extractor), \
         patch.object(frame_extraction_stage_gpu, "write_frames_batch", return_value=len(frame_paths)):
        success, error = frame_extraction_stage_gpu.run_frame_extraction_stage(
            mock_firestore, "dQw4w9WgXcQ", mock_queue
        )

    assert success is True, error
    update_call = video_doc_mock.update.call_args[0][0]
    assert "stages.frame_extraction.authenticity_vision_motion" in update_call
    assert "authenticity_signals.vision_motion" in update_call
    assert update_call["authenticity_signals.vision_motion"]["confidence"] in ("low", "medium")


def test_authenticity_pass_exception_never_fails_the_stage(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "AUTHENTICITY_CHECK_ENABLED", True)
    video_doc_mock, mock_firestore, mock_queue, frame_paths = _setup_common(tmp_path, monkeypatch)
    extractor = _make_extractor_mock(frame_paths)

    with patch.object(frame_extraction_stage_gpu.FrameExtractorFactory, "create", return_value=extractor), \
         patch.object(frame_extraction_stage_gpu, "write_frames_batch", return_value=len(frame_paths)), \
         patch("processing.film_authenticity.analyze_frame_signals", side_effect=RuntimeError("boom")):
        success, error = frame_extraction_stage_gpu.run_frame_extraction_stage(
            mock_firestore, "dQw4w9WgXcQ", mock_queue
        )

    assert success is True, error
    update_call = video_doc_mock.update.call_args[0][0]
    assert "stages.frame_extraction.authenticity_vision_motion" not in update_call
