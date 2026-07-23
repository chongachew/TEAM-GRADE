"""
Regression test for a real production bug: pose_stage_lite.py's multi-player
path called estimate_multi() (which returns (landmarks_dict, confidence)
tuples) but passed that straight into match_poses_to_tracks() (which expects
bare landmarks dicts and calls .values() on each element). Every frame threw
AttributeError, silently swallowed by the per-frame try/except, so multi-
player pose extraction always produced total_poses: 0 - first surfaced by a
real video (kJM5Uk9DtoQ) once MULTI_PLAYER_TRACKING_ENABLED went live in
production. Neither test_pose_track_matching.py (tests match_poses_to_tracks
in isolation with correctly-shaped dicts) nor
test_pose_stage_lite_streaming.py (every case forces
MULTI_PLAYER_TRACKING_ENABLED=False) exercised this integration boundary.
"""

from unittest.mock import MagicMock, patch

import cv2
import numpy as np

from config import settings
from ingest.stages import pose_stage_lite


def _write_frame(path):
    img = np.zeros((64, 64, 3), dtype=np.uint8)
    cv2.imwrite(str(path), img)


def _make_track_doc(frame_index, track_id, bbox):
    doc = MagicMock()
    doc.to_dict.return_value = {"frame_index": frame_index, "track_id": track_id, "bbox": bbox}
    return doc


def _landmarks(cx, cy, w=0.1, h=0.3):
    return {
        "nose": {"x": cx, "y": cy - h / 2, "confidence": 0.9},
        "left_hip": {"x": cx - w / 2, "y": cy + h / 2, "confidence": 0.9},
        "right_hip": {"x": cx + w / 2, "y": cy + h / 2, "confidence": 0.9},
    }


def _make_multiplayer_estimator():
    estimator = MagicMock()
    # Real estimate_multi() shape: list of (landmarks_dict, confidence) tuples.
    estimator.estimate_multi.return_value = [
        (_landmarks(0.2, 0.5), 0.9),
        (_landmarks(0.7, 0.5), 0.85),
    ]
    estimator.get_model_info.return_value = {"model_type": "lite", "model_size_mb": 2}
    estimator.close.return_value = None
    return estimator


def test_multiplayer_path_writes_poses_matched_to_tracks(tmp_path, monkeypatch):
    frames_dir = tmp_path / "frames"
    frames_dir.mkdir()
    _write_frame(frames_dir / "frame_000000.jpg")

    monkeypatch.setattr(settings, "get_frames_dir", lambda video_id: frames_dir)
    monkeypatch.setattr(settings, "MULTI_PLAYER_TRACKING_ENABLED", True)
    monkeypatch.setattr(settings, "POSE_MAX_PLAYERS", 6)
    monkeypatch.setattr(settings, "POSE_TRACK_IOU_THRESHOLD", 0.3)

    mock_firestore = MagicMock()
    mock_firestore.db.collection.return_value.document.return_value.collection.return_value.stream.return_value = [
        _make_track_doc(0, 5, [0.15 * 64, 0.35 * 64, 0.25 * 64, 0.65 * 64]),
        _make_track_doc(0, 9, [0.65 * 64, 0.35 * 64, 0.75 * 64, 0.65 * 64]),
    ]
    mock_queue = MagicMock()
    mock_queue.enqueue_video.return_value = True

    written_poses = []

    def _fake_write_pose_batch(firestore_client, video_id, poses, **kwargs):
        written_poses.extend(poses)
        return len(poses)

    with patch("processing.lightweight_pose.PoseEstimatorFactory.create", return_value=_make_multiplayer_estimator()), \
         patch.object(pose_stage_lite, "write_pose_batch", side_effect=_fake_write_pose_batch):
        success, error = pose_stage_lite.run_pose_stage_lite(mock_firestore, "dQw4w9WgXcQ", mock_queue)

    assert success is True, error
    # The real bug made this 0 every time - both poses should now match their
    # respective tracks (track 5 and track 9) and get written.
    assert len(written_poses) == 2
    assert sorted(p["track_id"] for p in written_poses) == [5, 9]

    update_call = mock_firestore.db.collection.return_value.document.return_value.update.call_args[0][0]
    assert update_call["stages.pose.total_poses"] == 2
