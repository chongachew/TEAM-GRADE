"""
Integration Tests for GET /api/reps/{video_id}/clip (highlight-tape clip cutting)

Mocks the ffmpeg subprocess call entirely - this dev environment has no
ffmpeg installed (confirmed in an earlier session), so these tests verify the
endpoint's plumbing (rep lookup, frame-to-seconds conversion, local-file
existence check, ffmpeg invocation shape, response/cleanup) rather than a
real video cut, which needs to be manually checked in an environment that
actually has ffmpeg.
"""

from unittest.mock import MagicMock, patch

import pytest


def _make_doc(data):
    doc = MagicMock()
    doc.to_dict.return_value = data
    return doc


def _wire_reps(mock_fs, reps_docs):
    reps_collection_mock = MagicMock()
    reps_collection_mock.stream.return_value = reps_docs

    def collection_side_effect(name):
        if name == "reps":
            return reps_collection_mock
        return MagicMock()

    doc_mock = MagicMock()
    doc_mock.collection.side_effect = collection_side_effect
    mock_fs.db.collection.return_value.document.return_value = doc_mock


def _fake_ffmpeg_success(cmd, **kwargs):
    """Mimics ffmpeg actually running: creates the requested output file."""
    output_path = cmd[-1]
    with open(output_path, "wb") as f:
        f.write(b"fake mp4 bytes")
    result = MagicMock()
    result.returncode = 0
    result.stderr = b""
    return result


class TestGetRepClip:
    @pytest.mark.endpoints
    @pytest.mark.integration
    def test_get_clip_success(self, client, mock_video_document, tmp_path):
        video_path = tmp_path / "dQw4w9WgXcQ.mp4"
        video_path.write_bytes(b"fake source video")
        rep_doc = _make_doc({"rep_index": 0, "track_id": 7, "start_frame": 30, "end_frame": 90})

        with patch("api.server.firestore_client") as mock_fs, \
             patch("config.settings.get_video_path", return_value=video_path), \
             patch("api.server.subprocess.run", side_effect=_fake_ffmpeg_success):
            mock_fs.get_video_status.return_value = mock_video_document
            _wire_reps(mock_fs, [rep_doc])

            response = client.get("/api/reps/dQw4w9WgXcQ/clip?track_id=7&rep_index=0")

            assert response.status_code == 200, response.text
            assert response.headers["content-type"] == "video/mp4"
            assert response.content == b"fake mp4 bytes"

    @pytest.mark.endpoints
    @pytest.mark.integration
    def test_get_clip_single_athlete_sentinel(self, client, mock_video_document, tmp_path):
        video_path = tmp_path / "dQw4w9WgXcQ.mp4"
        video_path.write_bytes(b"fake source video")
        rep_doc = _make_doc({"rep_index": 0, "start_frame": 30, "end_frame": 90})  # no track_id field

        with patch("api.server.firestore_client") as mock_fs, \
             patch("config.settings.get_video_path", return_value=video_path), \
             patch("api.server.subprocess.run", side_effect=_fake_ffmpeg_success):
            mock_fs.get_video_status.return_value = mock_video_document
            _wire_reps(mock_fs, [rep_doc])

            response = client.get("/api/reps/dQw4w9WgXcQ/clip?track_id=0&rep_index=0")

            assert response.status_code == 200, response.text

    @pytest.mark.endpoints
    @pytest.mark.integration
    def test_get_clip_ffmpeg_invoked_with_correct_seek_and_duration(self, client, mock_video_document, tmp_path):
        video_path = tmp_path / "dQw4w9WgXcQ.mp4"
        video_path.write_bytes(b"fake source video")
        # start_frame=30, end_frame=90, FPS=15 -> start=2.0s, duration=4.0s
        rep_doc = _make_doc({"rep_index": 0, "track_id": 7, "start_frame": 30, "end_frame": 90})

        with patch("api.server.firestore_client") as mock_fs, \
             patch("config.settings.get_video_path", return_value=video_path), \
             patch("api.server.subprocess.run", side_effect=_fake_ffmpeg_success) as mock_run:
            mock_fs.get_video_status.return_value = mock_video_document
            _wire_reps(mock_fs, [rep_doc])

            response = client.get("/api/reps/dQw4w9WgXcQ/clip?track_id=7&rep_index=0")

            assert response.status_code == 200
            cmd = mock_run.call_args[0][0]
            assert cmd[0] == "ffmpeg"
            assert cmd[cmd.index("-ss") + 1] == "2.0"
            assert cmd[cmd.index("-t") + 1] == "4.0"
            assert cmd[cmd.index("-c") + 1] == "copy"

    @pytest.mark.endpoints
    @pytest.mark.integration
    def test_get_clip_rep_not_found(self, client, mock_video_document):
        with patch("api.server.firestore_client") as mock_fs:
            mock_fs.get_video_status.return_value = mock_video_document
            _wire_reps(mock_fs, [])

            response = client.get("/api/reps/dQw4w9WgXcQ/clip?track_id=7&rep_index=0")

            assert response.status_code == 404

    @pytest.mark.endpoints
    @pytest.mark.integration
    def test_get_clip_video_not_found(self, client):
        with patch("api.server.firestore_client") as mock_fs:
            mock_fs.get_video_status.return_value = None

            response = client.get("/api/reps/dQw4w9WgXcQ/clip?track_id=7&rep_index=0")

            assert response.status_code == 404

    @pytest.mark.endpoints
    @pytest.mark.integration
    def test_get_clip_missing_local_source_file(self, client, mock_video_document, tmp_path):
        missing_path = tmp_path / "does_not_exist.mp4"
        rep_doc = _make_doc({"rep_index": 0, "track_id": 7, "start_frame": 30, "end_frame": 90})

        with patch("api.server.firestore_client") as mock_fs, \
             patch("config.settings.get_video_path", return_value=missing_path):
            mock_fs.get_video_status.return_value = mock_video_document
            _wire_reps(mock_fs, [rep_doc])

            response = client.get("/api/reps/dQw4w9WgXcQ/clip?track_id=7&rep_index=0")

            assert response.status_code == 404

    @pytest.mark.endpoints
    @pytest.mark.integration
    def test_get_clip_ffmpeg_failure_returns_500(self, client, mock_video_document, tmp_path):
        video_path = tmp_path / "dQw4w9WgXcQ.mp4"
        video_path.write_bytes(b"fake source video")
        rep_doc = _make_doc({"rep_index": 0, "track_id": 7, "start_frame": 30, "end_frame": 90})

        def _fake_ffmpeg_failure(cmd, **kwargs):
            result = MagicMock()
            result.returncode = 1
            result.stderr = b"ffmpeg exploded"
            return result

        with patch("api.server.firestore_client") as mock_fs, \
             patch("config.settings.get_video_path", return_value=video_path), \
             patch("api.server.subprocess.run", side_effect=_fake_ffmpeg_failure):
            mock_fs.get_video_status.return_value = mock_video_document
            _wire_reps(mock_fs, [rep_doc])

            response = client.get("/api/reps/dQw4w9WgXcQ/clip?track_id=7&rep_index=0")

            assert response.status_code == 500

    @pytest.mark.endpoints
    @pytest.mark.integration
    def test_get_clip_invalid_video_id(self, client):
        response = client.get("/api/reps/***/clip?track_id=7&rep_index=0")

        assert response.status_code == 400
