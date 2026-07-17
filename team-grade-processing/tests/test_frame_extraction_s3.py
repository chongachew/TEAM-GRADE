"""
Tests for frame_extraction_stage_gpu.py's Pass 2b S3 wiring: uploading
extracted frames to the shared media bucket after a successful extraction,
and falling back to an S3 download for the source video when it isn't
already local (a retry picked up by a different worker instance).

Mocked at the ingest.s3_client function boundary - not re-testing S3 itself
(see tests/test_s3_client.py for that). General stage plumbing (Firestore
wiring, authenticity hook) is covered by tests/test_frame_extraction_authenticity_hook.py.
"""

from pathlib import Path
from unittest.mock import MagicMock, patch

from ingest.stages import frame_extraction_stage_gpu


class FakeDoc:
    def __init__(self, data, exists=True):
        self._data = data
        self.exists = exists

    def to_dict(self):
        return self._data


def _make_extractor_mock(frame_count=4):
    frames_metadata = [
        {"frame_index": i, "timestamp_seconds": i / 15.0, "path": f"frame_{i:06d}.jpg", "width": 64, "height": 64}
        for i in range(frame_count)
    ]
    extractor = MagicMock()
    extractor.device_type = "CPU (FFmpeg)"
    extractor.extract_frames.return_value = (frame_count, frames_metadata)
    return extractor


def _setup_common(tmp_path):
    video_path = tmp_path / "video.mp4"
    video_path.write_bytes(b"fake video bytes")

    frames_dir = tmp_path / "frames"

    mock_firestore = MagicMock()
    video_doc_mock = MagicMock()
    video_doc_mock.get.return_value = FakeDoc({"download_path": str(video_path)})
    mock_firestore.db.collection.return_value.document.return_value = video_doc_mock

    mock_queue = MagicMock()
    mock_queue.enqueue_video.return_value = True

    return video_path, frames_dir, video_doc_mock, mock_firestore, mock_queue


def test_uploads_extracted_frames_to_s3_once_as_a_directory(tmp_path, monkeypatch):
    monkeypatch.setattr(frame_extraction_stage_gpu.settings, "AUTHENTICITY_CHECK_ENABLED", False)
    video_path, frames_dir, video_doc_mock, mock_firestore, mock_queue = _setup_common(tmp_path)
    monkeypatch.setattr(frame_extraction_stage_gpu.settings, "get_frames_dir", lambda video_id: frames_dir)
    extractor = _make_extractor_mock()

    with patch.object(frame_extraction_stage_gpu.FrameExtractorFactory, "create", return_value=extractor), \
         patch.object(frame_extraction_stage_gpu, "write_frames_batch", return_value=4), \
         patch.object(frame_extraction_stage_gpu, "upload_directory", return_value=4) as mock_upload:
        success, error = frame_extraction_stage_gpu.run_frame_extraction_stage(
            mock_firestore, "dQw4w9WgXcQ", mock_queue
        )

    assert success is True, error
    mock_upload.assert_called_once_with(frames_dir, "videos/dQw4w9WgXcQ/frames")


def test_frame_upload_failure_does_not_fail_the_stage(tmp_path, monkeypatch):
    monkeypatch.setattr(frame_extraction_stage_gpu.settings, "AUTHENTICITY_CHECK_ENABLED", False)
    video_path, frames_dir, video_doc_mock, mock_firestore, mock_queue = _setup_common(tmp_path)
    monkeypatch.setattr(frame_extraction_stage_gpu.settings, "get_frames_dir", lambda video_id: frames_dir)
    extractor = _make_extractor_mock()

    with patch.object(frame_extraction_stage_gpu.FrameExtractorFactory, "create", return_value=extractor), \
         patch.object(frame_extraction_stage_gpu, "write_frames_batch", return_value=4), \
         patch.object(frame_extraction_stage_gpu, "upload_directory", side_effect=RuntimeError("S3 is down")):
        success, error = frame_extraction_stage_gpu.run_frame_extraction_stage(
            mock_firestore, "dQw4w9WgXcQ", mock_queue
        )

    assert success is True, error


def test_falls_back_to_s3_download_when_video_not_local(tmp_path, monkeypatch):
    """The video-path read guard: if download_path's file isn't there (e.g. a
    retry landed on a different worker instance than the one that ran
    download_stage), ensure_video_local() is tried before giving up."""
    monkeypatch.setattr(frame_extraction_stage_gpu.settings, "AUTHENTICITY_CHECK_ENABLED", False)

    missing_video_path = tmp_path / "missing.mp4"
    recovered_video_path = tmp_path / "recovered.mp4"
    recovered_video_path.write_bytes(b"fetched from s3")
    frames_dir = tmp_path / "frames"

    mock_firestore = MagicMock()
    video_doc_mock = MagicMock()
    video_doc_mock.get.return_value = FakeDoc({"download_path": str(missing_video_path)})
    mock_firestore.db.collection.return_value.document.return_value = video_doc_mock

    mock_queue = MagicMock()
    mock_queue.enqueue_video.return_value = True

    monkeypatch.setattr(frame_extraction_stage_gpu.settings, "get_frames_dir", lambda video_id: frames_dir)
    extractor = _make_extractor_mock()

    with patch.object(frame_extraction_stage_gpu.FrameExtractorFactory, "create", return_value=extractor) as mock_create, \
         patch.object(frame_extraction_stage_gpu, "write_frames_batch", return_value=4), \
         patch.object(frame_extraction_stage_gpu, "upload_directory", return_value=4), \
         patch.object(frame_extraction_stage_gpu, "ensure_video_local", return_value=recovered_video_path) as mock_ensure:
        success, error = frame_extraction_stage_gpu.run_frame_extraction_stage(
            mock_firestore, "dQw4w9WgXcQ", mock_queue
        )

    assert success is True, error
    mock_ensure.assert_called_once_with("dQw4w9WgXcQ")
    # The recovered (S3-fetched) path, not the missing one, is what got
    # handed to the frame extractor.
    assert mock_create.call_args[0][0] == recovered_video_path


def test_video_still_missing_after_s3_fallback_fails_returns_video_file_not_found(tmp_path, monkeypatch):
    monkeypatch.setattr(frame_extraction_stage_gpu.settings, "AUTHENTICITY_CHECK_ENABLED", False)

    missing_video_path = tmp_path / "missing.mp4"
    frames_dir = tmp_path / "frames"

    mock_firestore = MagicMock()
    video_doc_mock = MagicMock()
    video_doc_mock.get.return_value = FakeDoc({"download_path": str(missing_video_path)})
    mock_firestore.db.collection.return_value.document.return_value = video_doc_mock

    mock_queue = MagicMock()

    monkeypatch.setattr(frame_extraction_stage_gpu.settings, "get_frames_dir", lambda video_id: frames_dir)

    with patch.object(frame_extraction_stage_gpu, "ensure_video_local", side_effect=RuntimeError("not in S3 either")):
        success, error = frame_extraction_stage_gpu.run_frame_extraction_stage(
            mock_firestore, "dQw4w9WgXcQ", mock_queue
        )

    assert success is False
    assert error == "VIDEO_FILE_NOT_FOUND"
