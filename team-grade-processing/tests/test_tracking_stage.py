"""
Tests for the tracking stage's Firestore wiring, with the real
DensityAwareMemoryTracker (SAM2-backed) mocked out - no real model load
needed. This file is about the stage's own logic: reading detections,
writing track/track-meta docs, per-play scoping, and enqueue.

tests/test_tracking.py (pre-existing) tests the tracker class itself, not
this handler - there was no dedicated handler-level test file before this.
"""

from unittest.mock import MagicMock, patch

import numpy as np

from ingest.stages import tracking_stage


class FakeTrack:
    def __init__(self, last_frame_seen, status="active"):
        self.last_frame_seen = last_frame_seen
        self.status = status


class FakeTracker:
    """Assigns one track_id per distinct detection dict identity, in the
    order first seen - enough to exercise the stage's own bookkeeping
    without needing the real SAM2-backed tracker.
    """

    def __init__(self):
        self.tracks = {}
        self._next_id = 0

    def process_frame(self, frame_idx, frame_rgb, frame_dets):
        results = []
        for det in frame_dets:
            track_id = self._next_id
            self._next_id += 1
            self.tracks[track_id] = FakeTrack(last_frame_seen=frame_idx)
            results.append({
                "track_id": track_id,
                "frame_index": frame_idx,
                "bbox": det["bbox"],
                "mask_area_px": 100,
                "confidence": det.get("confidence", 0.9),
                "matched_via": "new_track",
                "occlusion_state": "visible",
                "frames_since_last_seen": 0,
                "recovered_from_gap": 0,
            })
        return results


def _write_fake_frames(frames_dir, indices):
    import cv2
    frames_dir.mkdir(exist_ok=True)
    for i in indices:
        cv2.imwrite(str(frames_dir / f"frame_{i:06d}.jpg"), np.zeros((10, 10, 3), dtype=np.uint8))


def _mock_firestore(detections_by_call=None):
    mock_firestore = MagicMock()
    mock_firestore.db = MagicMock()
    detections_collection = mock_firestore.db.collection.return_value.document.return_value.collection.return_value
    detections_collection.stream.return_value = detections_by_call or []
    detections_collection.where.return_value.stream.return_value = detections_by_call or []
    return mock_firestore


def test_run_tracking_stage_writes_expected_doc_shape(tmp_path, monkeypatch):
    frames_dir = tmp_path / "frames"
    _write_fake_frames(frames_dir, range(2))
    monkeypatch.setattr(tracking_stage.settings, "get_frames_dir", lambda video_id: frames_dir)

    det_docs = [
        MagicMock(to_dict=lambda: {"frame_index": 0, "bbox": [0, 0, 10, 10], "confidence": 0.9}),
        MagicMock(to_dict=lambda: {"frame_index": 1, "bbox": [1, 1, 11, 11], "confidence": 0.8}),
    ]
    mock_firestore = _mock_firestore(det_docs)
    mock_queue = MagicMock()
    mock_queue.enqueue_video.return_value = True

    written = {}

    def fake_write_tracks_batch(firestore_client, video_id, tracks):
        written["tracks"] = tracks
        return len(tracks)

    with patch.object(tracking_stage, "reset_tracker", return_value=FakeTracker()), \
         patch.object(tracking_stage, "write_tracks_batch", side_effect=fake_write_tracks_batch), \
         patch.object(tracking_stage, "upsert_track_meta") as mock_upsert_meta:
        success, error = tracking_stage.run_tracking_stage(mock_firestore, "dQw4w9WgXcQ", mock_queue)

    assert success is True
    assert error is None

    tracks = written["tracks"]
    assert len(tracks) == 2
    assert [t["frame_index"] for t in tracks] == [0, 1]
    assert all(t["play_index"] is None for t in tracks)

    # upsert_track_meta called once per distinct track, with play_index=None.
    assert mock_upsert_meta.call_count == 2
    for call in mock_upsert_meta.call_args_list:
        assert call.kwargs["play_index"] is None

    mock_queue.enqueue_video.assert_called_once()
    _, kwargs = mock_queue.enqueue_video.call_args
    assert kwargs["stage"] == "pose"
    assert kwargs["play_index"] is None


def test_run_tracking_stage_clears_frame_range_before_writing(tmp_path, monkeypatch):
    # Regression test for a real production bug (2026-07-29): same stale-row
    # issue as detection_stage.py - write_tracks_batch upserts by
    # (video_id, frame_index, track_id) and never deletes, and a re-run's
    # track_ids don't even line up with a prior run's (fresh tracker per
    # run). The fix is to clear the frame range being (re)processed first.
    frames_dir = tmp_path / "frames"
    _write_fake_frames(frames_dir, range(2))
    monkeypatch.setattr(tracking_stage.settings, "get_frames_dir", lambda video_id: frames_dir)

    det_docs = [
        MagicMock(to_dict=lambda: {"frame_index": 0, "bbox": [0, 0, 10, 10], "confidence": 0.9}),
        MagicMock(to_dict=lambda: {"frame_index": 1, "bbox": [1, 1, 11, 11], "confidence": 0.8}),
    ]
    mock_firestore = _mock_firestore(det_docs)
    mock_queue = MagicMock()
    mock_queue.enqueue_video.return_value = True

    call_order = []

    def fake_clear(*args, **kwargs):
        call_order.append("clear")
        return 4

    def fake_write(*args, **kwargs):
        call_order.append("write")
        return 2

    with patch.object(tracking_stage, "reset_tracker", return_value=FakeTracker()), \
         patch.object(tracking_stage, "clear_tracks_for_frame_range", side_effect=fake_clear) as mock_clear, \
         patch.object(tracking_stage, "write_tracks_batch", side_effect=fake_write), \
         patch.object(tracking_stage, "upsert_track_meta"):
        success, error = tracking_stage.run_tracking_stage(mock_firestore, "dQw4w9WgXcQ", mock_queue)

    assert success is True, error
    mock_clear.assert_called_once_with(mock_firestore, "dQw4w9WgXcQ", 0, 1)
    # Clear must happen before write, not after.
    assert call_order == ["clear", "write"]


def test_run_tracking_stage_scopes_to_one_play(tmp_path, monkeypatch):
    # 6 frames on disk; queue item scoped to play_index=1 (frames 3-5).
    # Detections query is mocked to only return play 1's detections - the
    # stage itself is responsible for both filtering the query AND the
    # frame directory to the play's range.
    frames_dir = tmp_path / "frames"
    _write_fake_frames(frames_dir, range(6))
    monkeypatch.setattr(tracking_stage.settings, "get_frames_dir", lambda video_id: frames_dir)

    det_docs = [
        MagicMock(to_dict=lambda i=i: {"frame_index": i, "bbox": [0, 0, 10, 10], "confidence": 0.9})
        for i in (3, 4, 5)
    ]
    mock_firestore = _mock_firestore(det_docs)
    mock_queue = MagicMock()
    mock_queue.enqueue_video.return_value = True

    written = {}

    def fake_write_tracks_batch(firestore_client, video_id, tracks):
        written["tracks"] = tracks
        return len(tracks)

    with patch.object(tracking_stage, "get_play", return_value={"start_frame": 3, "end_frame": 5}), \
         patch.object(tracking_stage, "reset_tracker", return_value=FakeTracker()), \
         patch.object(tracking_stage, "write_tracks_batch", side_effect=fake_write_tracks_batch), \
         patch.object(tracking_stage, "upsert_track_meta") as mock_upsert_meta:
        success, error = tracking_stage.run_tracking_stage(
            mock_firestore, "dQw4w9WgXcQ", mock_queue, payload={"play_index": 1}
        )

    assert success is True
    tracks = written["tracks"]
    frame_indices = sorted(t["frame_index"] for t in tracks)
    assert frame_indices == [3, 4, 5]
    assert all(t["play_index"] == 1 for t in tracks)

    for call in mock_upsert_meta.call_args_list:
        assert call.kwargs["play_index"] == 1

    _, kwargs = mock_queue.enqueue_video.call_args
    assert kwargs["play_index"] == 1


def test_run_tracking_stage_missing_play_row_returns_error(tmp_path, monkeypatch):
    frames_dir = tmp_path / "frames"
    frames_dir.mkdir()
    monkeypatch.setattr(tracking_stage.settings, "get_frames_dir", lambda video_id: frames_dir)

    mock_firestore = _mock_firestore([])
    mock_queue = MagicMock()

    with patch.object(tracking_stage, "get_play", return_value=None):
        success, error = tracking_stage.run_tracking_stage(
            mock_firestore, "dQw4w9WgXcQ", mock_queue, payload={"play_index": 9}
        )

    assert success is False
    assert error == "PLAY_NOT_FOUND"
    mock_queue.enqueue_video.assert_not_called()


def test_run_tracking_stage_no_frames_returns_error(tmp_path, monkeypatch):
    empty_dir = tmp_path / "empty"
    empty_dir.mkdir()
    monkeypatch.setattr(tracking_stage.settings, "get_frames_dir", lambda video_id: empty_dir)

    mock_firestore = _mock_firestore([])
    mock_queue = MagicMock()

    success, error = tracking_stage.run_tracking_stage(mock_firestore, "dQw4w9WgXcQ", mock_queue)

    assert success is False
    assert error == "FRAMES_NOT_FOUND"


def test_run_tracking_stage_no_detections_returns_error(tmp_path, monkeypatch):
    frames_dir = tmp_path / "frames"
    _write_fake_frames(frames_dir, range(2))
    monkeypatch.setattr(tracking_stage.settings, "get_frames_dir", lambda video_id: frames_dir)

    mock_firestore = _mock_firestore([])
    mock_queue = MagicMock()

    success, error = tracking_stage.run_tracking_stage(mock_firestore, "dQw4w9WgXcQ", mock_queue)

    assert success is False
    assert error == "DETECTIONS_NOT_FOUND"
