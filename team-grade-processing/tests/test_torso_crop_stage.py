"""
Tests for torso_crop_stage.py's Pass 2b S3 wiring: uploading created torso
crops to the shared media bucket after the stage finishes, and falling back
to an S3 download for the frames directory when it isn't already local (a
retry picked up by a different worker instance).

Mocked at the ingest.s3_client function boundary - not re-testing S3 itself
(see tests/test_s3_client.py for that), and mocks TorsoCropper entirely -
its own cropping logic is out of scope here.
"""

from unittest.mock import MagicMock, patch

import cv2
import numpy as np

from ingest.stages import torso_crop_stage


def _make_doc(data):
    doc = MagicMock()
    doc.to_dict.return_value = data
    return doc


def _write_frame(path):
    img = np.zeros((64, 64, 3), dtype=np.uint8)
    cv2.imwrite(str(path), img)


def _setup(tmp_path, monkeypatch, num_frames=2):
    frames_dir = tmp_path / "frames"
    frames_dir.mkdir()
    torso_dir = tmp_path / "torso"

    frame_paths = []
    for i in range(num_frames):
        p = frames_dir / f"frame_{i:06d}.jpg"
        _write_frame(p)
        frame_paths.append(p)

    monkeypatch.setattr(torso_crop_stage.settings, "get_frames_dir", lambda video_id: frames_dir)
    monkeypatch.setattr(torso_crop_stage.settings, "get_torso_crops_dir", lambda video_id: torso_dir)

    pose_docs = [
        _make_doc({"frame_index": i, "landmarks": [{"x": 0.5, "y": 0.5}] * 33, "track_id": None})
        for i in range(num_frames)
    ]

    mock_firestore = MagicMock()

    def collection_side_effect(name):
        if name == "pose":
            mock_col = MagicMock()
            mock_col.stream.return_value = pose_docs
            return mock_col
        return MagicMock()

    doc_mock = MagicMock()
    doc_mock.collection.side_effect = collection_side_effect
    mock_firestore.db.collection.return_value.document.return_value = doc_mock

    mock_queue = MagicMock()
    mock_queue.enqueue_video.return_value = True

    mock_cropper = MagicMock()
    mock_cropper.crop_torso.return_value = np.ones((32, 32, 3), dtype=np.uint8)
    mock_cropper.get_torso_box.return_value = [0, 0, 32, 32]

    return frames_dir, torso_dir, mock_firestore, mock_queue, mock_cropper


def test_uploads_created_torso_crops_to_s3(tmp_path, monkeypatch):
    frames_dir, torso_dir, mock_firestore, mock_queue, mock_cropper = _setup(tmp_path, monkeypatch)

    with patch("processing.torso_cropper.TorsoCropper", return_value=mock_cropper), \
         patch.object(torso_crop_stage, "write_torso_crops_batch", return_value=2), \
         patch.object(torso_crop_stage, "ensure_frames_local"), \
         patch.object(torso_crop_stage, "upload_directory", return_value=2) as mock_upload:
        success, error = torso_crop_stage.run_torso_crop_stage(
            mock_firestore, "dQw4w9WgXcQ", mock_queue
        )

    assert success is True, error
    mock_upload.assert_called_once_with(torso_dir, "videos/dQw4w9WgXcQ/torso")


def test_no_upload_call_when_zero_crops_created(tmp_path, monkeypatch):
    frames_dir, torso_dir, mock_firestore, mock_queue, mock_cropper = _setup(tmp_path, monkeypatch)
    mock_cropper.crop_torso.return_value = None  # every frame fails to crop

    with patch("processing.torso_cropper.TorsoCropper", return_value=mock_cropper), \
         patch.object(torso_crop_stage, "write_torso_crops_batch", return_value=0), \
         patch.object(torso_crop_stage, "ensure_frames_local"), \
         patch.object(torso_crop_stage, "upload_directory") as mock_upload:
        success, error = torso_crop_stage.run_torso_crop_stage(
            mock_firestore, "dQw4w9WgXcQ", mock_queue
        )

    assert success is True, error
    mock_upload.assert_not_called()


def test_crop_upload_failure_does_not_fail_the_stage(tmp_path, monkeypatch):
    frames_dir, torso_dir, mock_firestore, mock_queue, mock_cropper = _setup(tmp_path, monkeypatch)

    with patch("processing.torso_cropper.TorsoCropper", return_value=mock_cropper), \
         patch.object(torso_crop_stage, "write_torso_crops_batch", return_value=2), \
         patch.object(torso_crop_stage, "ensure_frames_local"), \
         patch.object(torso_crop_stage, "upload_directory", side_effect=RuntimeError("S3 is down")):
        success, error = torso_crop_stage.run_torso_crop_stage(
            mock_firestore, "dQw4w9WgXcQ", mock_queue
        )

    assert success is True, error


def test_ensure_frames_local_called_before_globbing_frames(tmp_path, monkeypatch):
    """The frames-read defensive guard: ensure_frames_local() is tried (and
    its failure swallowed) before the stage's own FRAMES_NOT_FOUND check."""
    frames_dir, torso_dir, mock_firestore, mock_queue, mock_cropper = _setup(tmp_path, monkeypatch)

    with patch("processing.torso_cropper.TorsoCropper", return_value=mock_cropper), \
         patch.object(torso_crop_stage, "write_torso_crops_batch", return_value=2), \
         patch.object(torso_crop_stage, "ensure_frames_local") as mock_ensure, \
         patch.object(torso_crop_stage, "upload_directory", return_value=2):
        success, error = torso_crop_stage.run_torso_crop_stage(
            mock_firestore, "dQw4w9WgXcQ", mock_queue
        )

    assert success is True, error
    mock_ensure.assert_called_once_with("dQw4w9WgXcQ")


def test_run_torso_crop_stage_scopes_to_one_play(tmp_path, monkeypatch):
    # 6 frames on disk; queue item scoped to play_index=1 (frames 3-5). The
    # pose-docs query is mocked to only return play 1's poses - the stage
    # itself is responsible for both filtering the query AND the frame
    # dict lookup to the play's range.
    frames_dir, torso_dir, mock_firestore, mock_queue, mock_cropper = _setup(tmp_path, monkeypatch, num_frames=6)
    # crop_doc's crop_path field is built via crop_path.relative_to(PROJECT_ROOT) -
    # torso_dir must be a subpath of it for that call to succeed in this test env.
    monkeypatch.setattr(torso_crop_stage.settings, "PROJECT_ROOT", tmp_path)

    pose_docs = [
        _make_doc({"frame_index": i, "landmarks": [{"x": 0.5, "y": 0.5}] * 33, "track_id": None})
        for i in (3, 4, 5)
    ]

    def collection_side_effect(name):
        mock_col = MagicMock()
        if name == "pose":
            mock_col.stream.return_value = pose_docs
            mock_col.where.return_value.stream.return_value = pose_docs
        return mock_col

    doc_mock = MagicMock()
    doc_mock.collection.side_effect = collection_side_effect
    mock_firestore.db.collection.return_value.document.return_value = doc_mock

    written_crops = {}

    def fake_write_torso_crops_batch(firestore_client, video_id, crops):
        written_crops["crops"] = crops
        return len(crops)

    with patch("processing.torso_cropper.TorsoCropper", return_value=mock_cropper), \
         patch.object(torso_crop_stage, "get_play", return_value={"start_frame": 3, "end_frame": 5}), \
         patch.object(torso_crop_stage, "write_torso_crops_batch", side_effect=fake_write_torso_crops_batch), \
         patch.object(torso_crop_stage, "ensure_frames_local"), \
         patch.object(torso_crop_stage, "upload_directory", return_value=3):
        success, error = torso_crop_stage.run_torso_crop_stage(
            mock_firestore, "dQw4w9WgXcQ", mock_queue, payload={"play_index": 1}
        )

    assert success is True, error
    crops = written_crops["crops"]
    assert sorted(c["frame_index"] for c in crops) == [3, 4, 5]
    assert all(c["play_index"] == 1 for c in crops)

    _, kwargs = mock_queue.enqueue_video.call_args
    assert kwargs["play_index"] == 1


def test_run_torso_crop_stage_missing_play_row_returns_error(tmp_path, monkeypatch):
    frames_dir, torso_dir, mock_firestore, mock_queue, mock_cropper = _setup(tmp_path, monkeypatch)

    with patch.object(torso_crop_stage, "get_play", return_value=None):
        success, error = torso_crop_stage.run_torso_crop_stage(
            mock_firestore, "dQw4w9WgXcQ", mock_queue, payload={"play_index": 3}
        )

    assert success is False
    assert error == "PLAY_NOT_FOUND"
    mock_queue.enqueue_video.assert_not_called()


def test_ensure_frames_local_failure_is_non_fatal(tmp_path, monkeypatch):
    """If frames are genuinely missing both locally and in S3,
    ensure_frames_local raises - the stage must swallow that and fall
    through to its own existing FRAMES_NOT_FOUND handling, not crash."""
    frames_dir = tmp_path / "frames"  # deliberately never created/populated
    torso_dir = tmp_path / "torso"
    monkeypatch.setattr(torso_crop_stage.settings, "get_frames_dir", lambda video_id: frames_dir)
    monkeypatch.setattr(torso_crop_stage.settings, "get_torso_crops_dir", lambda video_id: torso_dir)

    mock_firestore = MagicMock()
    mock_queue = MagicMock()

    with patch.object(torso_crop_stage, "ensure_frames_local", side_effect=RuntimeError("not in S3 either")):
        success, error = torso_crop_stage.run_torso_crop_stage(
            mock_firestore, "dQw4w9WgXcQ", mock_queue
        )

    assert success is False
    assert error == "FRAMES_NOT_FOUND"
