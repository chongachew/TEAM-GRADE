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


def test_rep_start_end_frame_use_real_frame_index_not_list_position(monkeypatch):
    """Regression test: a rep's start_frame/end_frame must be the pose docs'
    real (sparse, offset) frame_index values, not this track's position
    within its own temporally-sparse per-track frame list.

    Previously the fast-trajectory path in segment_reps_from_pose_docs
    (processing/rep_extraction.py) stored segment_into_reps_fast's raw
    (start, end) list-position tuple directly as start_frame/end_frame. A
    track only ever detected in frames 687/700/746 (mid-video, e.g. play 6
    of a real multi-play video) got a rep with start_frame=0, end_frame=2 -
    nothing in the pose table's real frame_index range ever matched it again
    in biomechanics_stage_vectorized.py's frame_index BETWEEN start_frame AND
    end_frame query, so every rep's features were empty and every grade was
    0.0 in production.
    """
    monkeypatch.setattr(rep_extraction_stage.settings, "MULTI_PLAYER_TRACKING_ENABLED", True)

    poses_docs = [
        _make_pose_doc(687, track_id=0),
        _make_pose_doc(700, track_id=0),
        _make_pose_doc(746, track_id=0),
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
        mock_firestore.db.collection.return_value.document.return_value.collection.return_value.document.return_value.get.return_value.exists = False

        success, error = rep_extraction_stage.run_rep_extraction_stage(
            mock_firestore, "dQw4w9WgXcQ", mock_queue
        )

    assert success is True, error
    reps = written.get("reps")
    assert reps, "expected at least one rep to be written"
    for rep in reps:
        assert rep["start_frame"] >= 687, (
            f"start_frame={rep['start_frame']} looks like a list position, not a real frame_index"
        )
        assert rep["end_frame"] <= 746, (
            f"end_frame={rep['end_frame']} looks like a list position, not a real frame_index"
        )


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

    def fake_write_reps_batch(firestore_client, video_id, reps):
        return len(reps)

    with patch.object(rep_extraction_stage, "write_reps_batch", side_effect=fake_write_reps_batch):
        success, error = rep_extraction_stage.run_rep_extraction_stage(
            mock_firestore, "dQw4w9WgXcQ", mock_queue
        )

    assert success is True, error


def test_run_rep_extraction_stage_scopes_to_one_play(monkeypatch):
    monkeypatch.setattr(rep_extraction_stage.settings, "MULTI_PLAYER_TRACKING_ENABLED", False)

    poses_docs = [
        FakeDoc({
            "frame_index": i,
            "landmarks": {
                "left_hip": {"x": 0.3, "y": 0.5, "confidence": 0.9},
                "right_hip": {"x": 0.35, "y": 0.5, "confidence": 0.9},
            },
        })
        for i in (3, 4, 5)
    ]

    mock_firestore = MagicMock()
    mock_poses_collection = MagicMock()
    mock_poses_collection.where.return_value.stream.return_value = poses_docs
    mock_firestore.db.collection.return_value.document.return_value.collection.return_value = mock_poses_collection

    mock_queue = MagicMock()
    mock_queue.enqueue_video.return_value = True

    written = {}

    def fake_write_reps_batch(firestore_client, video_id, reps):
        written["reps"] = reps
        return len(reps)

    with patch.object(rep_extraction_stage, "get_play", return_value={"start_frame": 3, "end_frame": 5}), \
         patch.object(rep_extraction_stage, "write_reps_batch", side_effect=fake_write_reps_batch), \
         patch.object(rep_extraction_stage, "update_stage_status") as mock_update_status:
        success, error = rep_extraction_stage.run_rep_extraction_stage(
            mock_firestore, "dQw4w9WgXcQ", mock_queue, payload={"play_index": 1}
        )

    assert success is True, error
    if written.get("reps"):
        assert all(rep["play_index"] == 1 for rep in written["reps"])
    assert mock_update_status.call_args.kwargs["play_index"] == 1
    assert mock_queue.enqueue_video.call_args.kwargs["play_index"] == 1


def test_partial_write_reps_batch_failure_is_reported_not_silently_succeeded(monkeypatch):
    """Regression test: write_reps_batch swallows its own DB errors and
    returns a short count instead of raising (e.g. a per-row ON CONFLICT
    failure drops that row/batch silently - see its docstring). Previously
    run_rep_extraction_stage ignored write_reps_batch's return value
    entirely, so a partial write still marked the stage completed and
    enqueued biomechanics against stale/missing rep rows, with no retry ever
    triggered. The stage must now report FIRESTORE_ERROR (triggering the
    normal retry path) whenever fewer reps were written than requested.
    """
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

    with patch.object(rep_extraction_stage, "write_reps_batch", return_value=1) as mock_write, \
         patch.object(rep_extraction_stage, "update_stage_status") as mock_update_status:
        mock_firestore.db.collection.return_value.document.return_value.collection.return_value.document.return_value.get.return_value.exists = False

        success, error = rep_extraction_stage.run_rep_extraction_stage(
            mock_firestore, "dQw4w9WgXcQ", mock_queue
        )

    assert mock_write.called
    assert success is False
    assert error == "FIRESTORE_ERROR"
    mock_update_status.assert_not_called()
    mock_queue.enqueue_video.assert_not_called()


def test_run_rep_extraction_stage_missing_play_row_returns_error():
    mock_firestore = MagicMock()
    mock_queue = MagicMock()

    with patch.object(rep_extraction_stage, "get_play", return_value=None):
        success, error = rep_extraction_stage.run_rep_extraction_stage(
            mock_firestore, "dQw4w9WgXcQ", mock_queue, payload={"play_index": 6}
        )

    assert success is False
    assert error == "PLAY_NOT_FOUND"
    mock_queue.enqueue_video.assert_not_called()


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
