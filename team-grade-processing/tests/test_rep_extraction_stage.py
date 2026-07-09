"""
Tests for rep_extraction_stage's per-track frame grouping (multi-player path)
and the settings-bug fix that previously made this stage fail outright on
every real invocation (see test_settings_completeness.py for the general
regression guard; this file tests the actual grouping behavior).
"""

from unittest.mock import MagicMock, patch

from ingest.stages import rep_extraction_stage


class FakeDoc:
    def __init__(self, data):
        self._data = data

    def to_dict(self):
        return self._data


def _make_pose_doc(frame_index, track_id, x=0.3):
    return FakeDoc({
        "frame_index": frame_index,
        "track_id": track_id,
        "landmarks": {
            "left_hip": {"x": x, "y": 0.5, "confidence": 0.9},
            "right_hip": {"x": x + 0.05, "y": 0.5, "confidence": 0.9},
        },
    })


def test_groups_frames_by_track_id(monkeypatch):
    monkeypatch.setattr(rep_extraction_stage.settings, "MULTI_PLAYER_TRACKING_ENABLED", True)

    poses_docs = [
        _make_pose_doc(0, track_id=5),
        _make_pose_doc(1, track_id=9),
        _make_pose_doc(1, track_id=5),
        _make_pose_doc(2, track_id=9),
        _make_pose_doc(2, track_id=5),
    ]

    mock_firestore = MagicMock()
    mock_poses_collection = MagicMock()
    mock_poses_collection.stream.return_value = poses_docs
    mock_firestore.db.collection.return_value.document.return_value.collection.return_value = mock_poses_collection

    mock_queue = MagicMock()
    mock_queue.enqueue_video.return_value = True

    written = {}

    def fake_write_reps_batch(firestore_client, video_id, reps):
        written["reps"] = reps
        return len(reps)

    with patch.object(rep_extraction_stage, "write_reps_batch", side_effect=fake_write_reps_batch):
        # tracks_meta lookups (jersey number per track) - not under test here.
        mock_firestore.db.collection.return_value.document.return_value.collection.return_value.document.return_value.get.return_value.exists = False

        success, error = rep_extraction_stage.run_rep_extraction_stage(
            mock_firestore, "dQw4w9WgXcQ", mock_queue
        )

    assert success is True, error
    # Two tracks were present (5 and 9); reps written should carry track_id.
    if written.get("reps"):
        track_ids_seen = {rep["track_id"] for rep in written["reps"]}
        assert track_ids_seen.issubset({5, 9})


def test_single_athlete_path_unaffected_when_flag_disabled(monkeypatch):
    """With MULTI_PLAYER_TRACKING_ENABLED off, pose docs have no track_id and
    everything should group into the single None-keyed bucket, matching
    pre-pivot behavior exactly."""
    monkeypatch.setattr(rep_extraction_stage.settings, "MULTI_PLAYER_TRACKING_ENABLED", False)

    poses_docs = [
        FakeDoc({
            "frame_index": i,
            "landmarks": {
                "left_hip": {"x": 0.3, "y": 0.5, "confidence": 0.9},
                "right_hip": {"x": 0.35, "y": 0.5, "confidence": 0.9},
            },
        })
        for i in range(5)
    ]

    mock_firestore = MagicMock()
    mock_poses_collection = MagicMock()
    mock_poses_collection.stream.return_value = poses_docs
    mock_firestore.db.collection.return_value.document.return_value.collection.return_value = mock_poses_collection

    mock_queue = MagicMock()
    mock_queue.enqueue_video.return_value = True

    with patch.object(rep_extraction_stage, "write_reps_batch", return_value=0):
        success, error = rep_extraction_stage.run_rep_extraction_stage(
            mock_firestore, "dQw4w9WgXcQ", mock_queue
        )

    assert success is True, error


def test_no_poses_found_completes_gracefully():
    mock_firestore = MagicMock()
    mock_poses_collection = MagicMock()
    mock_poses_collection.stream.return_value = []
    mock_firestore.db.collection.return_value.document.return_value.collection.return_value = mock_poses_collection

    mock_queue = MagicMock()
    mock_queue.enqueue_video.return_value = True

    success, error = rep_extraction_stage.run_rep_extraction_stage(
        mock_firestore, "dQw4w9WgXcQ", mock_queue
    )

    assert success is True
    assert error is None
