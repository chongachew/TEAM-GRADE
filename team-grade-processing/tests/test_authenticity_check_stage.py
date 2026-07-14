"""
Tests for authenticity_check_stage's Firestore wiring (video-path lookup,
writing the file-integrity verdict, enqueueing the next stage). The heuristic
detection itself is covered by test_film_authenticity.py - these tests mock
analyze_file_integrity entirely and focus on the stage's plumbing, especially
the soft-flag contract: a flagged=True verdict must still return (True, None).
"""

from unittest.mock import MagicMock, patch

from ingest.stages import authenticity_check_stage


class FakeDoc:
    def __init__(self, data, exists=True):
        self._data = data
        self.exists = exists

    def to_dict(self):
        return self._data


def test_writes_verdict_and_enqueues_next_stage_when_clean(tmp_path):
    video_path = tmp_path / "video.mp4"
    video_path.write_bytes(b"fake video bytes")

    mock_firestore = MagicMock()
    video_doc_mock = MagicMock()
    video_doc_mock.get.return_value = FakeDoc({"download_path": str(video_path)})
    mock_firestore.db.collection.return_value.document.return_value = video_doc_mock

    mock_queue = MagicMock()
    mock_queue.enqueue_video.return_value = True

    clean_result = {"flagged": False, "confidence": "low", "reasons": [], "raw": {}}

    with patch("processing.film_authenticity.analyze_file_integrity", return_value=clean_result):
        success, error = authenticity_check_stage.run_authenticity_check_stage(
            mock_firestore, "dQw4w9WgXcQ", mock_queue
        )

    assert success is True, error
    update_call = video_doc_mock.update.call_args[0][0]
    assert update_call["stages.authenticity_check.flagged"] is False
    assert update_call["authenticity_signals.file_integrity"]["flagged"] is False
    mock_queue.enqueue_video.assert_called_once()
    assert mock_queue.enqueue_video.call_args.kwargs["stage"] == "frame_extraction"


def test_flagged_verdict_is_soft_flag_not_a_stage_failure(tmp_path):
    """The whole point of this stage: a flag is data, never a pipeline failure."""
    video_path = tmp_path / "video.mp4"
    video_path.write_bytes(b"fake video bytes")

    mock_firestore = MagicMock()
    video_doc_mock = MagicMock()
    video_doc_mock.get.return_value = FakeDoc({"download_path": str(video_path)})
    mock_firestore.db.collection.return_value.document.return_value = video_doc_mock

    mock_queue = MagicMock()
    mock_queue.enqueue_video.return_value = True

    flagged_result = {
        "flagged": True,
        "confidence": "medium",
        "reasons": ["sparse_keyframe_interval_20.0s"],
        "raw": {},
    }

    with patch("processing.film_authenticity.analyze_file_integrity", return_value=flagged_result):
        success, error = authenticity_check_stage.run_authenticity_check_stage(
            mock_firestore, "dQw4w9WgXcQ", mock_queue
        )

    assert success is True, error
    update_call = video_doc_mock.update.call_args[0][0]
    assert update_call["stages.authenticity_check.flagged"] is True
    assert update_call["authenticity_signals.file_integrity"]["flagged"] is True
    mock_queue.enqueue_video.assert_called_once()


def test_video_doc_not_found_fails_gracefully():
    mock_firestore = MagicMock()
    video_doc_mock = MagicMock()
    video_doc_mock.get.return_value = FakeDoc({}, exists=False)
    mock_firestore.db.collection.return_value.document.return_value = video_doc_mock

    mock_queue = MagicMock()

    success, error = authenticity_check_stage.run_authenticity_check_stage(
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

    success, error = authenticity_check_stage.run_authenticity_check_stage(
        mock_firestore, "dQw4w9WgXcQ", mock_queue
    )

    assert success is False
    assert error == "VIDEO_FILE_NOT_FOUND"
    mock_queue.enqueue_video.assert_not_called()


def test_invalid_video_id_fails_validation():
    mock_firestore = MagicMock()
    mock_queue = MagicMock()
    mock_queue.enqueue_video.return_value = True

    success, error = authenticity_check_stage.run_authenticity_check_stage(
        mock_firestore, "***", mock_queue
    )

    assert success is False
    assert error is not None


def test_routes_to_whistle_detection_when_enabled(tmp_path, monkeypatch):
    """STAGE_NEXT is computed at import time from settings.WHISTLE_DETECTION_ENABLED -
    directly overriding the module attribute is the simplest way to exercise
    the other branch without reimporting the module under a patched env var."""
    video_path = tmp_path / "video.mp4"
    video_path.write_bytes(b"fake video bytes")

    mock_firestore = MagicMock()
    video_doc_mock = MagicMock()
    video_doc_mock.get.return_value = FakeDoc({"download_path": str(video_path)})
    mock_firestore.db.collection.return_value.document.return_value = video_doc_mock

    mock_queue = MagicMock()
    mock_queue.enqueue_video.return_value = True

    clean_result = {"flagged": False, "confidence": "low", "reasons": [], "raw": {}}

    monkeypatch.setattr(authenticity_check_stage, "STAGE_NEXT", "whistle_detection")
    with patch("processing.film_authenticity.analyze_file_integrity", return_value=clean_result):
        success, error = authenticity_check_stage.run_authenticity_check_stage(
            mock_firestore, "dQw4w9WgXcQ", mock_queue
        )

    assert success is True, error
    assert mock_queue.enqueue_video.call_args.kwargs["stage"] == "whistle_detection"
