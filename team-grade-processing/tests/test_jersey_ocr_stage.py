"""
Regression test for a real production bug: get_ocr_instance() in
jersey_ocr_stage.py constructed JerseyOCR(ocr_engine=..., language=...,
gpu=True), but JerseyOCR.__init__() (processing/jersey_ocr.py) only ever
accepted ocr_engine and language - no gpu kwarg exists there (GPU vs. CPU is
decided internally by _load_easyocr(), which hardcodes gpu=True on
easyocr.Reader with an automatic fallback to CPU on RuntimeError). This threw
TypeError on every single call, mapped to error_code "MODEL_LOAD_FAILED" by
run_jersey_ocr_stage's except block - deterministic, not transient, so every
retry failed identically. First surfaced for real once a production video
(kJM5Uk9DtoQ) reached this stage with real torso-crop data after the
multi-player pose fix.
"""

from unittest.mock import MagicMock, patch

import cv2
import numpy as np

from config import settings
from ingest.stages import jersey_ocr_stage


def test_get_ocr_instance_constructs_with_only_supported_kwargs(monkeypatch):
    jersey_ocr_stage._OCR_CACHE = None
    monkeypatch.setattr(settings, "OCR_ENGINE", "easyocr")
    monkeypatch.setattr(settings, "OCR_LANGUAGE", "en")

    with patch("processing.jersey_ocr.JerseyOCR") as MockJerseyOCR:
        MockJerseyOCR.return_value = MagicMock()
        instance = jersey_ocr_stage.get_ocr_instance()

    MockJerseyOCR.assert_called_once_with(ocr_engine="easyocr", language="en")
    assert instance is MockJerseyOCR.return_value


def test_get_ocr_instance_rejects_unsupported_gpu_kwarg_regression(monkeypatch):
    """Reproduces the real bug directly against the real JerseyOCR class
    (not a mock) - the actual TypeError this bug threw in production."""
    from processing.jersey_ocr import JerseyOCR

    jersey_ocr_stage._OCR_CACHE = None
    monkeypatch.setattr(settings, "OCR_ENGINE", "easyocr")
    monkeypatch.setattr(settings, "OCR_LANGUAGE", "en")

    try:
        JerseyOCR(ocr_engine="easyocr", language="en", gpu=True)
        raised = False
    except TypeError:
        raised = True

    assert raised, "JerseyOCR still doesn't accept 'gpu' - if this now passes, get_ocr_instance() no longer needs to omit it"


def _make_crop_doc(frame_index, track_id):
    doc = MagicMock()
    doc.to_dict.return_value = {"frame_index": frame_index, "track_id": track_id}
    return doc


def test_run_jersey_ocr_stage_scopes_to_one_play(tmp_path, monkeypatch):
    crops_dir = tmp_path / "torso"
    crops_dir.mkdir()
    for frame_index, track_id in [(3, 0), (4, 0)]:
        cv2.imwrite(str(crops_dir / f"torso_{frame_index:06d}_{track_id:03d}.jpg"), np.zeros((10, 10, 3), dtype=np.uint8))

    monkeypatch.setattr(jersey_ocr_stage.settings, "get_torso_crops_dir", lambda video_id: crops_dir)
    monkeypatch.setattr(jersey_ocr_stage.settings, "MULTI_PLAYER_TRACKING_ENABLED", True)

    crop_docs = [_make_crop_doc(3, 0), _make_crop_doc(4, 0)]
    mock_firestore = MagicMock()
    mock_firestore.db.collection.return_value.document.return_value.collection.return_value.where.return_value.stream.return_value = crop_docs

    mock_queue = MagicMock()
    mock_queue.enqueue_video.return_value = True

    fake_ocr = MagicMock()
    fake_ocr.read_jersey_number.return_value = ("12", 0.95)

    with patch.object(jersey_ocr_stage, "get_play", return_value={"start_frame": 3, "end_frame": 5}), \
         patch.object(jersey_ocr_stage, "get_ocr_instance", return_value=fake_ocr), \
         patch.object(jersey_ocr_stage, "ensure_torso_crops_local"), \
         patch.object(jersey_ocr_stage, "write_jersey_number_for_track") as mock_write_track, \
         patch.object(jersey_ocr_stage, "update_stage_status") as mock_update_status:
        success, error = jersey_ocr_stage.run_jersey_ocr_stage(
            mock_firestore, "dQw4w9WgXcQ", mock_queue, payload={"play_index": 1}
        )

    assert success is True, error

    mock_write_track.assert_called_once()
    _, kwargs = mock_write_track.call_args
    assert kwargs["play_index"] == 1

    assert mock_update_status.call_args.kwargs["play_index"] == 1
    assert mock_queue.enqueue_video.call_args.kwargs["play_index"] == 1


def test_run_jersey_ocr_stage_missing_play_row_returns_error(tmp_path, monkeypatch):
    mock_firestore = MagicMock()
    mock_queue = MagicMock()

    with patch.object(jersey_ocr_stage, "get_play", return_value=None):
        success, error = jersey_ocr_stage.run_jersey_ocr_stage(
            mock_firestore, "dQw4w9WgXcQ", mock_queue, payload={"play_index": 2}
        )

    assert success is False
    assert error == "PLAY_NOT_FOUND"
    mock_queue.enqueue_video.assert_not_called()


def test_run_jersey_ocr_stage_no_crops_skips_to_next_stage_with_play_index(tmp_path, monkeypatch):
    mock_firestore = MagicMock()
    mock_firestore.db.collection.return_value.document.return_value.collection.return_value.where.return_value.stream.return_value = []
    mock_queue = MagicMock()
    mock_queue.enqueue_video.return_value = True

    with patch.object(jersey_ocr_stage, "get_play", return_value={"start_frame": 0, "end_frame": 10}), \
         patch.object(jersey_ocr_stage, "update_stage_status") as mock_update_status:
        success, error = jersey_ocr_stage.run_jersey_ocr_stage(
            mock_firestore, "dQw4w9WgXcQ", mock_queue, payload={"play_index": 0}
        )

    assert success is True, error
    assert mock_update_status.call_args.kwargs["play_index"] == 0
    assert mock_queue.enqueue_video.call_args.kwargs["play_index"] == 0
