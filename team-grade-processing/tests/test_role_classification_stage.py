"""
Tests for the role classification stage's Firestore wiring, with
processing.uniform_classifier.classify_uniform mocked out - the classifier's
own real-image behavior is exercised in test_uniform_classifier.py; this
file is about the stage's own logic (per-track sampling/aggregation, doc-id
shape, enqueue).
"""

from unittest.mock import MagicMock, patch

import cv2
import numpy as np

from config import settings
from ingest.stages import role_classification_stage


def _make_crop_doc(frame_index, track_id):
    doc = MagicMock()
    doc.to_dict.return_value = {"frame_index": frame_index, "track_id": track_id}
    return doc


def test_run_role_classification_stage_writes_majority_vote_role(tmp_path, monkeypatch):
    crops_dir = tmp_path / "torso"
    crops_dir.mkdir()
    # Track 5: 3 crops, 2 real (referee-classified) + 1 missing on disk.
    for frame_index in (10, 20):
        cv2.imwrite(str(crops_dir / f"torso_{frame_index:06d}_005.jpg"), np.zeros((20, 20, 3), dtype=np.uint8))

    monkeypatch.setattr(role_classification_stage.settings, "get_torso_crops_dir", lambda video_id: crops_dir)
    monkeypatch.setattr(role_classification_stage.settings, "MULTI_PLAYER_TRACKING_ENABLED", True)

    crop_docs = [_make_crop_doc(10, 5), _make_crop_doc(20, 5), _make_crop_doc(30, 5)]
    mock_firestore = MagicMock()
    mock_firestore.db.collection.return_value.document.return_value.collection.return_value.stream.return_value = crop_docs

    mock_queue = MagicMock()
    mock_queue.enqueue_video.return_value = True

    with patch.object(role_classification_stage, "get_play", return_value=None), \
         patch("processing.uniform_classifier.classify_uniform", return_value=("referee", 0.8)), \
         patch.object(role_classification_stage, "ensure_torso_crops_local"), \
         patch.object(role_classification_stage, "upsert_track_meta") as mock_upsert, \
         patch.object(role_classification_stage, "update_stage_status") as mock_update_status:
        success, error = role_classification_stage.run_role_classification_stage(
            mock_firestore, "dQw4w9WgXcQ", mock_queue
        )

    assert success is True, error

    mock_upsert.assert_called_once()
    args, kwargs = mock_upsert.call_args
    # (firestore_client, video_id, track_id, fields, play_index=...)
    assert args[2] == 5
    assert args[3]["role"] == "referee"
    assert args[3]["role_confidence"] == 0.8

    mock_queue.enqueue_video.assert_called_once()
    assert mock_queue.enqueue_video.call_args.kwargs["stage"] == "jersey_ocr"


def test_run_role_classification_stage_scopes_to_one_play(tmp_path, monkeypatch):
    crops_dir = tmp_path / "torso"
    crops_dir.mkdir()
    cv2.imwrite(str(crops_dir / "torso_000003_000.jpg"), np.zeros((20, 20, 3), dtype=np.uint8))

    monkeypatch.setattr(role_classification_stage.settings, "get_torso_crops_dir", lambda video_id: crops_dir)
    monkeypatch.setattr(role_classification_stage.settings, "MULTI_PLAYER_TRACKING_ENABLED", True)

    crop_docs = [_make_crop_doc(3, 0)]
    mock_firestore = MagicMock()
    mock_firestore.db.collection.return_value.document.return_value.collection.return_value.where.return_value.stream.return_value = crop_docs

    mock_queue = MagicMock()
    mock_queue.enqueue_video.return_value = True

    with patch.object(role_classification_stage, "get_play", return_value={"start_frame": 3, "end_frame": 5}), \
         patch("processing.uniform_classifier.classify_uniform", return_value=("player", 0.7)), \
         patch.object(role_classification_stage, "ensure_torso_crops_local"), \
         patch.object(role_classification_stage, "upsert_track_meta") as mock_upsert, \
         patch.object(role_classification_stage, "update_stage_status") as mock_update_status:
        success, error = role_classification_stage.run_role_classification_stage(
            mock_firestore, "dQw4w9WgXcQ", mock_queue, payload={"play_index": 1}
        )

    assert success is True, error
    assert mock_upsert.call_args.kwargs["play_index"] == 1
    assert mock_update_status.call_args.kwargs["play_index"] == 1
    assert mock_queue.enqueue_video.call_args.kwargs["play_index"] == 1


def test_run_role_classification_stage_missing_play_row_returns_error():
    mock_firestore = MagicMock()
    mock_queue = MagicMock()

    with patch.object(role_classification_stage, "get_play", return_value=None):
        success, error = role_classification_stage.run_role_classification_stage(
            mock_firestore, "dQw4w9WgXcQ", mock_queue, payload={"play_index": 2}
        )

    assert success is False
    assert error == "PLAY_NOT_FOUND"
    mock_queue.enqueue_video.assert_not_called()


def test_run_role_classification_stage_no_crops_skips_to_next_stage(monkeypatch):
    monkeypatch.setattr(role_classification_stage.settings, "MULTI_PLAYER_TRACKING_ENABLED", True)

    mock_firestore = MagicMock()
    mock_firestore.db.collection.return_value.document.return_value.collection.return_value.stream.return_value = []
    mock_queue = MagicMock()
    mock_queue.enqueue_video.return_value = True

    with patch.object(role_classification_stage, "get_play", return_value=None), \
         patch.object(role_classification_stage, "update_stage_status") as mock_update_status:
        success, error = role_classification_stage.run_role_classification_stage(
            mock_firestore, "dQw4w9WgXcQ", mock_queue
        )

    assert success is True, error
    mock_queue.enqueue_video.assert_called_once()
    assert mock_queue.enqueue_video.call_args.kwargs["stage"] == "jersey_ocr"


def test_run_role_classification_stage_skips_when_not_multi_player(monkeypatch):
    monkeypatch.setattr(role_classification_stage.settings, "MULTI_PLAYER_TRACKING_ENABLED", False)

    mock_firestore = MagicMock()
    mock_queue = MagicMock()
    mock_queue.enqueue_video.return_value = True

    with patch.object(role_classification_stage, "update_stage_status") as mock_update_status:
        success, error = role_classification_stage.run_role_classification_stage(
            mock_firestore, "dQw4w9WgXcQ", mock_queue
        )

    assert success is True, error
    # Never even queried Firestore for crops - single-athlete mode has no
    # role ambiguity to resolve.
    mock_firestore.db.collection.assert_not_called()
    mock_queue.enqueue_video.assert_called_once()
    assert mock_queue.enqueue_video.call_args.kwargs["stage"] == "jersey_ocr"
