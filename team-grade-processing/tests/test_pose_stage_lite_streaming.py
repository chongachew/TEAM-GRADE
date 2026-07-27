"""
Tests for pose_stage_lite.py's incremental pose-write behavior: instead of
holding every frame's pose result in memory and writing it all in one big
batch at the very end, it now flushes every POSE_BATCH_FRAME_COUNT frames -
so pose data becomes queryable while the stage is still running (this is
what makes get_analysis's provisional-plays preview possible - see
test_analysis_endpoints.py's TestProvisionalAnalysis).

The specific bug-risk this change introduces if done carelessly: the
in-memory `poses` list gets cleared after each flush, so anything that used
to read `len(poses)` for the final "how many poses total" count would only
ever see the last partial batch, not the true total. These tests verify the
true running total (poses_written_total) is what actually gets reported.
"""

from unittest.mock import MagicMock, patch

import cv2
import numpy as np

from config import settings
from ingest.stages import pose_stage_lite


def _write_frame(path):
    img = np.zeros((64, 64, 3), dtype=np.uint8)
    cv2.imwrite(str(path), img)


def _make_estimator(confidence=0.9):
    estimator = MagicMock()
    estimator.estimate.return_value = ({"nose": {"x": 0.5, "y": 0.5, "confidence": confidence}}, confidence)
    estimator.get_model_info.return_value = {"model_type": "lite", "model_size_mb": 2}
    estimator.close.return_value = None
    return estimator


def _fake_write_pose_batch(firestore_client, video_id, poses, **kwargs):
    return len(poses)


def test_flushes_in_multiple_batches_and_reports_true_total(tmp_path, monkeypatch):
    frames_dir = tmp_path / "frames"
    frames_dir.mkdir()
    # POSE_BATCH_FRAME_COUNT default is 10 - 25 frames means 2 mid-loop
    # flushes (at 10, at 20) plus a final flush of the remaining 5.
    for i in range(25):
        _write_frame(frames_dir / f"frame_{i:06d}.jpg")

    monkeypatch.setattr(settings, "get_frames_dir", lambda video_id: frames_dir)
    monkeypatch.setattr(settings, "MULTI_PLAYER_TRACKING_ENABLED", False)
    monkeypatch.setattr(settings, "POSE_BATCH_FRAME_COUNT", 10)

    mock_firestore = MagicMock()
    mock_queue = MagicMock()
    mock_queue.enqueue_video.return_value = True

    with patch("processing.lightweight_pose.PoseEstimatorFactory.create", return_value=_make_estimator()), \
         patch.object(pose_stage_lite, "write_pose_batch", side_effect=_fake_write_pose_batch) as mock_write, \
         patch.object(pose_stage_lite, "update_stage_status") as mock_update_status:
        success, error = pose_stage_lite.run_pose_stage_lite(mock_firestore, "dQw4w9WgXcQ", mock_queue)

    assert success is True, error
    assert mock_write.call_count == 3  # two full batches of 10 + one final batch of 5

    # true total, not just the last partial batch
    assert mock_update_status.call_args.kwargs["metadata"]["total_poses"] == 25

    assert mock_queue.enqueue_video.call_args.kwargs["metadata"]["pose_count"] == 25


def test_single_flush_when_frame_count_is_under_the_batch_size(tmp_path, monkeypatch):
    frames_dir = tmp_path / "frames"
    frames_dir.mkdir()
    for i in range(4):
        _write_frame(frames_dir / f"frame_{i:06d}.jpg")

    monkeypatch.setattr(settings, "get_frames_dir", lambda video_id: frames_dir)
    monkeypatch.setattr(settings, "MULTI_PLAYER_TRACKING_ENABLED", False)
    monkeypatch.setattr(settings, "POSE_BATCH_FRAME_COUNT", 10)

    mock_firestore = MagicMock()
    mock_queue = MagicMock()
    mock_queue.enqueue_video.return_value = True

    with patch("processing.lightweight_pose.PoseEstimatorFactory.create", return_value=_make_estimator()), \
         patch.object(pose_stage_lite, "write_pose_batch", side_effect=_fake_write_pose_batch) as mock_write, \
         patch.object(pose_stage_lite, "update_stage_status") as mock_update_status:
        success, error = pose_stage_lite.run_pose_stage_lite(mock_firestore, "dQw4w9WgXcQ", mock_queue)

    assert success is True, error
    assert mock_write.call_count == 1  # everything fits in the final flush, no mid-loop flush ever triggers

    assert mock_update_status.call_args.kwargs["metadata"]["total_poses"] == 4


def test_run_pose_stage_scopes_to_one_play(tmp_path, monkeypatch):
    frames_dir = tmp_path / "frames"
    frames_dir.mkdir()
    # 6 frames on disk; queue item scoped to play_index=1 (frames 3-5).
    for i in range(6):
        _write_frame(frames_dir / f"frame_{i:06d}.jpg")

    monkeypatch.setattr(settings, "get_frames_dir", lambda video_id: frames_dir)
    monkeypatch.setattr(settings, "MULTI_PLAYER_TRACKING_ENABLED", False)
    monkeypatch.setattr(settings, "POSE_BATCH_FRAME_COUNT", 10)

    mock_firestore = MagicMock()
    mock_queue = MagicMock()
    mock_queue.enqueue_video.return_value = True

    written_poses = []

    def _capture_write_pose_batch(firestore_client, video_id, poses, **kwargs):
        written_poses.extend(poses)
        return len(poses)

    with patch("processing.lightweight_pose.PoseEstimatorFactory.create", return_value=_make_estimator()), \
         patch.object(pose_stage_lite, "get_play", return_value={"start_frame": 3, "end_frame": 5}), \
         patch.object(pose_stage_lite, "write_pose_batch", side_effect=_capture_write_pose_batch), \
         patch.object(pose_stage_lite, "update_stage_status") as mock_update_status:
        success, error = pose_stage_lite.run_pose_stage_lite(
            mock_firestore, "dQw4w9WgXcQ", mock_queue, payload={"play_index": 1}
        )

    assert success is True, error
    # Only frames 3/4/5 processed, not 0/1/2.
    assert sorted(p["frame_index"] for p in written_poses) == [3, 4, 5]
    assert all(p["play_index"] == 1 for p in written_poses)
    assert mock_update_status.call_args.kwargs["play_index"] == 1
    assert mock_queue.enqueue_video.call_args.kwargs["play_index"] == 1


def test_run_pose_stage_missing_play_row_returns_error(tmp_path, monkeypatch):
    frames_dir = tmp_path / "frames"
    frames_dir.mkdir()
    monkeypatch.setattr(settings, "get_frames_dir", lambda video_id: frames_dir)
    monkeypatch.setattr(settings, "MULTI_PLAYER_TRACKING_ENABLED", False)

    mock_firestore = MagicMock()
    mock_queue = MagicMock()

    with patch.object(pose_stage_lite, "get_play", return_value=None):
        success, error = pose_stage_lite.run_pose_stage_lite(
            mock_firestore, "dQw4w9WgXcQ", mock_queue, payload={"play_index": 4}
        )

    assert success is False
    assert error == "PLAY_NOT_FOUND"
    mock_queue.enqueue_video.assert_not_called()


def test_streaming_flush_failure_is_non_fatal(tmp_path, monkeypatch):
    """A flush failing mid-loop shouldn't abort the whole stage - the final
    flush and stage completion still happen, matching this stage's existing
    per-frame error tolerance elsewhere."""
    frames_dir = tmp_path / "frames"
    frames_dir.mkdir()
    for i in range(15):
        _write_frame(frames_dir / f"frame_{i:06d}.jpg")

    monkeypatch.setattr(settings, "get_frames_dir", lambda video_id: frames_dir)
    monkeypatch.setattr(settings, "MULTI_PLAYER_TRACKING_ENABLED", False)
    monkeypatch.setattr(settings, "POSE_BATCH_FRAME_COUNT", 10)

    mock_firestore = MagicMock()
    mock_queue = MagicMock()
    mock_queue.enqueue_video.return_value = True

    with patch("processing.lightweight_pose.PoseEstimatorFactory.create", return_value=_make_estimator()), \
         patch.object(pose_stage_lite, "write_pose_batch", side_effect=RuntimeError("boom")):
        success, error = pose_stage_lite.run_pose_stage_lite(mock_firestore, "dQw4w9WgXcQ", mock_queue)

    # Every flush attempt (mid-loop and final) raises in this test, so no
    # poses ever actually get persisted - but the stage itself must not crash.
    assert success is False
    assert error == "FIRESTORE_ERROR"
