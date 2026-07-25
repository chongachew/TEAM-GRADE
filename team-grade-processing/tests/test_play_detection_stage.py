"""
Tests for the play_detection stage's queue/schema wiring, with the real
OCR/camera-motion signal extraction mocked out (no real EasyOCR/frames
inference needed here - the signals themselves are validated separately,
see scripts/ground_truth/ and adaptive-finding-planet.md). This file is
about the stage's own logic: writing `plays` rows and enqueueing one
`detection` job per play.
"""

from unittest.mock import MagicMock, patch

import numpy as np

from ingest.stages import play_detection_stage


def _write_fake_frames(frames_dir, count):
    import cv2
    frames_dir.mkdir(exist_ok=True)
    for i in range(count):
        cv2.imwrite(str(frames_dir / f"frame_{i:06d}.jpg"), np.zeros((10, 10, 3), dtype=np.uint8))


def _mock_firestore():
    mock_firestore = MagicMock()
    mock_firestore.db = MagicMock()
    mock_firestore.db.collection.return_value.document.return_value.collection.return_value.stream.return_value = []
    return mock_firestore


def test_writes_plays_and_enqueues_one_detection_job_per_play(tmp_path, monkeypatch):
    frames_dir = tmp_path / "frames"
    _write_fake_frames(frames_dir, 300)
    monkeypatch.setattr(play_detection_stage.settings, "get_frames_dir", lambda video_id: frames_dir)

    mock_firestore = _mock_firestore()
    mock_queue = MagicMock()
    mock_queue.enqueue_video.return_value = True

    written_plays = {}

    def fake_write_plays_batch(firestore_client, video_id, play_docs):
        written_plays["docs"] = play_docs
        return len(play_docs)

    with patch.object(play_detection_stage, "extract_ocr_candidates", return_value=[(50, 50 / 15.0)]), \
         patch.object(play_detection_stage, "extract_camera_motion_candidates_from_rows", return_value=[(150, 150 / 15.0)]), \
         patch.object(play_detection_stage, "write_plays_batch", side_effect=fake_write_plays_batch):
        success, error = play_detection_stage.run_play_detection_stage(
            mock_firestore, "dQw4w9WgXcQ", mock_queue
        )

    assert success is True
    assert error is None

    docs = written_plays["docs"]
    # 2 merged candidates (frame 50, frame 150) -> one range PER candidate:
    # [0,149] (first play's start forced to 0) and [150,299] (last play runs
    # to the video's real last frame).
    assert len(docs) == 2
    assert docs[0]["start_frame"] == 0
    assert docs[0]["end_frame"] == 149
    assert docs[1]["start_frame"] == 150
    assert docs[1]["end_frame"] == 299
    assert all(d["detection_method"] == "ocr+camera_motion" for d in docs)

    assert mock_queue.enqueue_video.call_count == 2
    for call in mock_queue.enqueue_video.call_args_list:
        _, kwargs = call
        assert kwargs["stage"] == "detection"
    play_indices = {call.kwargs["play_index"] for call in mock_queue.enqueue_video.call_args_list}
    assert play_indices == {0, 1}


def test_no_candidates_falls_back_to_single_whole_video_play(tmp_path, monkeypatch):
    frames_dir = tmp_path / "frames"
    _write_fake_frames(frames_dir, 100)
    monkeypatch.setattr(play_detection_stage.settings, "get_frames_dir", lambda video_id: frames_dir)

    mock_firestore = _mock_firestore()
    mock_queue = MagicMock()
    mock_queue.enqueue_video.return_value = True

    written_plays = {}

    def fake_write_plays_batch(firestore_client, video_id, play_docs):
        written_plays["docs"] = play_docs
        return len(play_docs)

    with patch.object(play_detection_stage, "extract_ocr_candidates", return_value=[]), \
         patch.object(play_detection_stage, "extract_camera_motion_candidates_from_rows", return_value=[]), \
         patch.object(play_detection_stage, "write_plays_batch", side_effect=fake_write_plays_batch):
        success, error = play_detection_stage.run_play_detection_stage(
            mock_firestore, "dQw4w9WgXcQ", mock_queue
        )

    assert success is True
    docs = written_plays["docs"]
    assert len(docs) == 1
    assert docs[0]["play_index"] == 0
    assert docs[0]["start_frame"] == 0
    assert docs[0]["end_frame"] == 99
    assert docs[0]["detection_method"] == "fallback_whole_video"
    mock_queue.enqueue_video.assert_called_once()


def test_frames_not_found_fails_gracefully(tmp_path, monkeypatch):
    empty_dir = tmp_path / "empty"
    empty_dir.mkdir()
    monkeypatch.setattr(play_detection_stage.settings, "get_frames_dir", lambda video_id: empty_dir)

    mock_firestore = _mock_firestore()
    mock_queue = MagicMock()

    success, error = play_detection_stage.run_play_detection_stage(
        mock_firestore, "dQw4w9WgXcQ", mock_queue
    )

    assert success is False
    assert error == "FRAMES_NOT_FOUND"
    mock_queue.enqueue_video.assert_not_called()


def test_partial_enqueue_failure_returns_false(tmp_path, monkeypatch):
    frames_dir = tmp_path / "frames"
    _write_fake_frames(frames_dir, 600)
    monkeypatch.setattr(play_detection_stage.settings, "get_frames_dir", lambda video_id: frames_dir)

    mock_firestore = _mock_firestore()
    mock_queue = MagicMock()
    # First enqueue succeeds, second fails.
    mock_queue.enqueue_video.side_effect = [True, False]

    # Two candidates far enough apart (frame 50 = 3.3s, frame 500 = 33.3s)
    # that merge_and_dedup's 5s window does NOT collapse them - 2 distinct
    # play ranges, matching write_plays_batch's return value below.
    with patch.object(play_detection_stage, "extract_ocr_candidates", return_value=[(50, 50 / 15.0)]), \
         patch.object(play_detection_stage, "extract_camera_motion_candidates_from_rows", return_value=[(500, 500 / 15.0)]), \
         patch.object(play_detection_stage, "write_plays_batch", return_value=2):
        success, error = play_detection_stage.run_play_detection_stage(
            mock_firestore, "dQw4w9WgXcQ", mock_queue
        )

    assert success is False
    assert error == "QUEUE_ERROR"
    assert mock_queue.enqueue_video.call_count == 2


def test_invalid_video_id_fails_validation():
    mock_firestore = _mock_firestore()
    mock_queue = MagicMock()

    success, error = play_detection_stage.run_play_detection_stage(
        mock_firestore, "../../etc/passwd", mock_queue
    )

    assert success is False
    assert error is not None
    mock_queue.enqueue_video.assert_not_called()
