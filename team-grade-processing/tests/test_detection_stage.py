"""
Tests for the detection stage's Firestore wiring, with RF-DETR's model mocked
out (no real inference / model download needed) - the RF-DETR API itself is
exercised for real in test_detection_stage_live.py-style manual checks; this
file is about the stage's own logic (batching, doc-id shape, enqueue).
"""

from unittest.mock import MagicMock, patch
import numpy as np

from ingest.stages import detection_stage


class FakeDetections:
    def __init__(self, xyxy, confidence, class_names):
        self.xyxy = np.array(xyxy, dtype=float)
        self.confidence = np.array(confidence, dtype=float)
        self.data = {"class_name": np.array(class_names, dtype=object)}


def test_run_detection_stage_writes_expected_doc_shape(tmp_path, monkeypatch):
    # Two fake frames on disk.
    frames_dir = tmp_path / "frames"
    frames_dir.mkdir()
    import cv2
    for i in range(2):
        cv2.imwrite(str(frames_dir / f"frame_{i:06d}.jpg"), np.zeros((100, 100, 3), dtype=np.uint8))

    monkeypatch.setattr(detection_stage.settings, "get_frames_dir", lambda video_id: frames_dir)

    fake_model = MagicMock()
    fake_model.predict.return_value = [
        FakeDetections(xyxy=[[10, 10, 50, 90]], confidence=[0.9], class_names=["person"]),
        FakeDetections(
            xyxy=[[5, 5, 40, 80], [0, 0, 10, 10]],
            confidence=[0.85, 0.6],
            class_names=["person", "sports ball"],
        ),
    ]

    mock_firestore = MagicMock()
    mock_firestore.db = MagicMock()
    mock_queue = MagicMock()
    mock_queue.enqueue_video.return_value = True

    written_docs = {}

    def fake_write_detections_batch(firestore_client, video_id, detections):
        written_docs["detections"] = detections
        return len(detections)

    with patch.object(detection_stage, "get_detection_model", return_value=fake_model), \
         patch.object(detection_stage, "write_detections_batch", side_effect=fake_write_detections_batch):
        success, error = detection_stage.run_detection_stage(
            mock_firestore, "dQw4w9WgXcQ", mock_queue
        )

    assert success is True
    assert error is None

    detections = written_docs["detections"]
    # Only "person" class kept; the "sports ball" detection is filtered out.
    assert len(detections) == 2
    assert all(d["class_name"] == "person" for d in detections)
    assert detections[0]["frame_index"] == 0
    assert detections[0]["detection_index"] == 0
    assert detections[1]["frame_index"] == 1
    assert detections[1]["detection_index"] == 0  # re-indexed after filtering out the ball

    mock_queue.enqueue_video.assert_called_once()
    _, kwargs = mock_queue.enqueue_video.call_args
    assert kwargs["stage"] == "tracking"


def test_run_detection_stage_scopes_to_one_play(tmp_path, monkeypatch):
    # 6 frames on disk spanning two "plays" (frames 0-2, frames 3-5); the
    # queue item is scoped to play_index=1 (frames 3-5) only.
    frames_dir = tmp_path / "frames"
    frames_dir.mkdir()
    import cv2
    for i in range(6):
        cv2.imwrite(str(frames_dir / f"frame_{i:06d}.jpg"), np.zeros((10, 10, 3), dtype=np.uint8))

    monkeypatch.setattr(detection_stage.settings, "get_frames_dir", lambda video_id: frames_dir)

    fake_model = MagicMock()
    fake_model.predict.return_value = [
        FakeDetections(xyxy=[[10, 10, 50, 90]], confidence=[0.9], class_names=["person"])
        for _ in range(3)
    ]

    mock_firestore = MagicMock()
    mock_firestore.db = MagicMock()
    mock_queue = MagicMock()
    mock_queue.enqueue_video.return_value = True

    written_docs = {}

    def fake_write_detections_batch(firestore_client, video_id, detections):
        written_docs["detections"] = detections
        return len(detections)

    with patch.object(detection_stage, "get_detection_model", return_value=fake_model), \
         patch.object(detection_stage, "get_play", return_value={"start_frame": 3, "end_frame": 5}), \
         patch.object(detection_stage, "write_detections_batch", side_effect=fake_write_detections_batch):
        success, error = detection_stage.run_detection_stage(
            mock_firestore, "dQw4w9WgXcQ", mock_queue, payload={"play_index": 1}
        )

    assert success is True
    assert error is None

    detections = written_docs["detections"]
    # Only frames 3/4/5 processed - not frames 0/1/2, which belong to the
    # other play.
    frame_indices = sorted(d["frame_index"] for d in detections)
    assert frame_indices == [3, 4, 5]
    assert all(d["play_index"] == 1 for d in detections)

    mock_queue.enqueue_video.assert_called_once()
    _, kwargs = mock_queue.enqueue_video.call_args
    assert kwargs["stage"] == "tracking"
    assert kwargs["play_index"] == 1


def test_run_detection_stage_missing_play_row_returns_error(tmp_path, monkeypatch):
    frames_dir = tmp_path / "frames"
    frames_dir.mkdir()
    monkeypatch.setattr(detection_stage.settings, "get_frames_dir", lambda video_id: frames_dir)

    mock_firestore = MagicMock()
    mock_firestore.db = MagicMock()
    mock_queue = MagicMock()

    with patch.object(detection_stage, "get_play", return_value=None):
        success, error = detection_stage.run_detection_stage(
            mock_firestore, "dQw4w9WgXcQ", mock_queue, payload={"play_index": 7}
        )

    assert success is False
    assert error == "PLAY_NOT_FOUND"
    mock_queue.enqueue_video.assert_not_called()


def test_run_detection_stage_no_frames_returns_error(tmp_path, monkeypatch):
    empty_dir = tmp_path / "empty"
    empty_dir.mkdir()
    monkeypatch.setattr(detection_stage.settings, "get_frames_dir", lambda video_id: empty_dir)

    mock_firestore = MagicMock()
    mock_firestore.db = MagicMock()
    mock_queue = MagicMock()

    success, error = detection_stage.run_detection_stage(mock_firestore, "dQw4w9WgXcQ", mock_queue)

    assert success is False
    assert error == "FRAMES_NOT_FOUND"


def test_run_detection_stage_field_boundary_filter_drops_off_field_detections(tmp_path, monkeypatch):
    # One real frame: a green "field" rectangle on a gray background, with
    # one detection whose foot-point lands on the field and one whose
    # foot-point lands in the gray background (crowd/sideline).
    frames_dir = tmp_path / "frames"
    frames_dir.mkdir()
    import cv2
    frame = np.full((400, 600, 3), (60, 60, 60), dtype=np.uint8)
    cv2.rectangle(frame, (100, 100), (500, 350), (40, 180, 40), thickness=-1)
    cv2.imwrite(str(frames_dir / "frame_000000.jpg"), frame)

    monkeypatch.setattr(detection_stage.settings, "get_frames_dir", lambda video_id: frames_dir)
    monkeypatch.setattr(detection_stage.settings, "FIELD_BOUNDARY_FILTER_ENABLED", True)
    monkeypatch.setattr(detection_stage.settings, "FIELD_BOUNDARY_LINE_REFINEMENT_ENABLED", False)

    fake_model = MagicMock()
    fake_model.predict.return_value = [
        FakeDetections(
            # On-field: bbox bottom-center at (300, 300), well inside the
            # green rectangle. Off-field: bbox bottom-center at (300, 20),
            # in the gray background above the field.
            xyxy=[[280, 250, 320, 300], [280, 0, 320, 20]],
            confidence=[0.9, 0.9],
            class_names=["person", "person"],
        ),
    ]

    mock_firestore = MagicMock()
    mock_firestore.db = MagicMock()
    mock_queue = MagicMock()
    mock_queue.enqueue_video.return_value = True

    written_docs = {}

    def fake_write_detections_batch(firestore_client, video_id, detections):
        written_docs["detections"] = detections
        return len(detections)

    with patch.object(detection_stage, "get_detection_model", return_value=fake_model), \
         patch.object(detection_stage, "write_detections_batch", side_effect=fake_write_detections_batch):
        success, error = detection_stage.run_detection_stage(
            mock_firestore, "dQw4w9WgXcQ", mock_queue
        )

    assert success is True
    assert error is None

    detections = written_docs["detections"]
    assert len(detections) == 1
    assert detections[0]["bbox"] == [280, 250, 320, 300]


def test_run_detection_stage_clears_frame_range_before_writing(tmp_path, monkeypatch):
    # Regression test for a real production bug (2026-07-29): a re-run that
    # finds FEWER detections for a frame than a prior run did left the extra
    # old rows stranded, since write_detections_batch only upserts by
    # (video_id, frame_index, detection_index) and never deletes. The fix is
    # to clear the frame range being (re)processed before writing.
    frames_dir = tmp_path / "frames"
    frames_dir.mkdir()
    import cv2
    for i in range(3):
        cv2.imwrite(str(frames_dir / f"frame_{i:06d}.jpg"), np.zeros((10, 10, 3), dtype=np.uint8))

    monkeypatch.setattr(detection_stage.settings, "get_frames_dir", lambda video_id: frames_dir)

    fake_model = MagicMock()
    fake_model.predict.return_value = [
        FakeDetections(xyxy=[[10, 10, 50, 90]], confidence=[0.9], class_names=["person"])
        for _ in range(3)
    ]

    mock_firestore = MagicMock()
    mock_firestore.db = MagicMock()
    mock_queue = MagicMock()
    mock_queue.enqueue_video.return_value = True

    call_order = []

    def fake_clear(*args, **kwargs):
        call_order.append("clear")
        return 5

    def fake_write(*args, **kwargs):
        call_order.append("write")
        return 3

    with patch.object(detection_stage, "get_detection_model", return_value=fake_model), \
         patch.object(detection_stage, "clear_detections_for_frame_range", side_effect=fake_clear) as mock_clear, \
         patch.object(detection_stage, "write_detections_batch", side_effect=fake_write):
        success, error = detection_stage.run_detection_stage(
            mock_firestore, "dQw4w9WgXcQ", mock_queue
        )

    assert success is True, error

    mock_clear.assert_called_once_with(mock_firestore, "dQw4w9WgXcQ", 0, 2)
    # Clear must happen before write, not after - otherwise it would wipe
    # out the very rows it was just supposed to protect.
    assert call_order == ["clear", "write"]


def test_run_detection_stage_field_boundary_filter_off_by_default(tmp_path, monkeypatch):
    # Same scene as above, but the flag is left at its default (off) -
    # both detections (on-field and off-field) must survive unfiltered.
    frames_dir = tmp_path / "frames"
    frames_dir.mkdir()
    import cv2
    frame = np.full((400, 600, 3), (60, 60, 60), dtype=np.uint8)
    cv2.rectangle(frame, (100, 100), (500, 350), (40, 180, 40), thickness=-1)
    cv2.imwrite(str(frames_dir / "frame_000000.jpg"), frame)

    monkeypatch.setattr(detection_stage.settings, "get_frames_dir", lambda video_id: frames_dir)
    assert detection_stage.settings.FIELD_BOUNDARY_FILTER_ENABLED is False

    fake_model = MagicMock()
    fake_model.predict.return_value = [
        FakeDetections(
            xyxy=[[280, 250, 320, 300], [280, 0, 320, 20]],
            confidence=[0.9, 0.9],
            class_names=["person", "person"],
        ),
    ]

    mock_firestore = MagicMock()
    mock_firestore.db = MagicMock()
    mock_queue = MagicMock()
    mock_queue.enqueue_video.return_value = True

    written_docs = {}

    def fake_write_detections_batch(firestore_client, video_id, detections):
        written_docs["detections"] = detections
        return len(detections)

    with patch.object(detection_stage, "get_detection_model", return_value=fake_model), \
         patch.object(detection_stage, "write_detections_batch", side_effect=fake_write_detections_batch):
        success, error = detection_stage.run_detection_stage(
            mock_firestore, "dQw4w9WgXcQ", mock_queue
        )

    assert success is True
    assert len(written_docs["detections"]) == 2
