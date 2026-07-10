"""
Tests for whistle_detection_stage's Firestore wiring (video-path lookup,
writing detected events, enqueueing the next stage). The detection algorithm
itself is covered by test_audio_analyzer.py - these tests mock AudioAnalyzer
entirely and focus on the stage's plumbing.
"""

from pathlib import Path
from unittest.mock import MagicMock, patch

from ingest.stages import whistle_detection_stage


class FakeDoc:
    def __init__(self, data, exists=True):
        self._data = data
        self.exists = exists

    def to_dict(self):
        return self._data


def test_writes_events_and_enqueues_next_stage(tmp_path, monkeypatch):
    video_path = tmp_path / "video.mp4"
    video_path.write_bytes(b"fake video bytes")

    mock_firestore = MagicMock()
    video_doc_mock = MagicMock()
    video_doc_mock.get.return_value = FakeDoc({"download_path": str(video_path)})
    mock_firestore.db.collection.return_value.document.return_value = video_doc_mock

    mock_queue = MagicMock()
    mock_queue.enqueue_video.return_value = True

    fake_events = [
        {"timestamp": 1.0, "confidence": 0.9, "onset_strength": 0.8,
         "spectral_centroid": 3000.0, "duration": 0.2, "type": "whistle_onset"},
    ]
    mock_analyzer = MagicMock()
    mock_analyzer.process_file.return_value = {
        "events": fake_events,
        "metadata": {"processing_time_sec": 0.05},
    }

    with patch("processing.audio_analyzer.AudioAnalyzer", return_value=mock_analyzer), \
         patch.object(whistle_detection_stage, "write_whistle_events_batch", return_value=1) as mock_write:
        success, error = whistle_detection_stage.run_whistle_detection_stage(
            mock_firestore, "dQw4w9WgXcQ", mock_queue
        )

    assert success is True, error
    mock_analyzer.process_file.assert_called_once_with(str(video_path))
    mock_write.assert_called_once()
    call_args = mock_write.call_args[0]
    assert call_args[1] == "dQw4w9WgXcQ"
    assert call_args[2] == fake_events
    mock_queue.enqueue_video.assert_called_once()
    assert mock_queue.enqueue_video.call_args.kwargs["stage"] == "frame_extraction"


def test_video_doc_not_found_fails_gracefully():
    mock_firestore = MagicMock()
    video_doc_mock = MagicMock()
    video_doc_mock.get.return_value = FakeDoc({}, exists=False)
    mock_firestore.db.collection.return_value.document.return_value = video_doc_mock

    mock_queue = MagicMock()

    success, error = whistle_detection_stage.run_whistle_detection_stage(
        mock_firestore, "dQw4w9WgXcQ", mock_queue
    )

    assert success is False
    assert error == "VIDEO_DOC_NOT_FOUND"
    mock_queue.enqueue_video.assert_not_called()


def test_video_file_missing_fails_gracefully():
    mock_firestore = MagicMock()
    video_doc_mock = MagicMock()
    video_doc_mock.get.return_value = FakeDoc({"download_path": "/nonexistent/path/video.mp4"})
    mock_firestore.db.collection.return_value.document.return_value = video_doc_mock

    mock_queue = MagicMock()

    success, error = whistle_detection_stage.run_whistle_detection_stage(
        mock_firestore, "dQw4w9WgXcQ", mock_queue
    )

    assert success is False
    assert error == "VIDEO_FILE_NOT_FOUND"
    mock_queue.enqueue_video.assert_not_called()


def test_analyzer_exception_returns_whistle_detection_failed(tmp_path):
    video_path = tmp_path / "video.mp4"
    video_path.write_bytes(b"fake video bytes")

    mock_firestore = MagicMock()
    video_doc_mock = MagicMock()
    video_doc_mock.get.return_value = FakeDoc({"download_path": str(video_path)})
    mock_firestore.db.collection.return_value.document.return_value = video_doc_mock

    mock_queue = MagicMock()

    with patch("processing.audio_analyzer.AudioAnalyzer", side_effect=RuntimeError("boom")):
        success, error = whistle_detection_stage.run_whistle_detection_stage(
            mock_firestore, "dQw4w9WgXcQ", mock_queue
        )

    assert success is False
    assert error == "WHISTLE_DETECTION_FAILED"
    mock_queue.enqueue_video.assert_not_called()


def test_invalid_video_id_fails_validation():
    mock_firestore = MagicMock()
    mock_queue = MagicMock()
    mock_queue.enqueue_video.return_value = True

    success, error = whistle_detection_stage.run_whistle_detection_stage(
        mock_firestore, "***", mock_queue
    )

    assert success is False
    assert error is not None
