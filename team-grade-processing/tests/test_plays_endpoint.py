"""
Integration tests for GET /api/plays/{video_id} (per-play redesign, Phase D).
Mirrors tests/test_analysis_endpoints.py's conventions: mock
api.server.firestore_client directly, wire .db.collection(...).document(...)
.collection(name).stream() per-subcollection.
"""

import pytest
from unittest.mock import MagicMock, patch


def _make_doc(data):
    doc = MagicMock()
    doc.to_dict.return_value = data
    return doc


def _wire_plays(mock_fs, play_docs):
    def collection_side_effect(name):
        col_mock = MagicMock()
        if name == "plays":
            col_mock.stream.return_value = [_make_doc(d) for d in play_docs]
        return col_mock

    doc_mock = MagicMock()
    doc_mock.collection.side_effect = collection_side_effect
    mock_fs.db.collection.return_value.document.return_value = doc_mock


class TestPlaysEndpoint:
    @pytest.mark.endpoints
    @pytest.mark.integration
    def test_returns_plays_sorted_by_play_index(self, client, mock_video_document):
        with patch("api.server.firestore_client") as mock_fs:
            mock_fs.get_video_status.return_value = mock_video_document
            _wire_plays(mock_fs, [
                {"play_index": 1, "start_frame": 150, "end_frame": 299, "status": "processing"},
                {"play_index": 0, "start_frame": 0, "end_frame": 149, "status": "completed"},
            ])

            response = client.get("/api/plays/dQw4w9WgXcQ")

        assert response.status_code == 200
        data = response.json()
        assert [p["play_index"] for p in data["plays"]] == [0, 1]
        assert data["plays"][0]["status"] == "completed"
        assert data["plays"][1]["status"] == "processing"

    @pytest.mark.endpoints
    @pytest.mark.integration
    def test_empty_plays_list_is_not_a_404(self, client, mock_video_document):
        """A video that exists but has no plays yet (single-athlete mode, or
        play_detection hasn't run) returns an empty list, not a 404 - same
        distinction get_analysis makes."""
        with patch("api.server.firestore_client") as mock_fs:
            mock_fs.get_video_status.return_value = mock_video_document
            _wire_plays(mock_fs, [])

            response = client.get("/api/plays/dQw4w9WgXcQ")

        assert response.status_code == 200
        data = response.json()
        assert data["plays"] == []

    @pytest.mark.endpoints
    @pytest.mark.integration
    def test_nonexistent_video_returns_404(self, client):
        with patch("api.server.firestore_client") as mock_fs:
            mock_fs.get_video_status.return_value = None

            response = client.get("/api/plays/dQw4w9WgXcQ")

        assert response.status_code == 404

    @pytest.mark.endpoints
    @pytest.mark.integration
    def test_overall_grade_passes_through_from_video_row(self, client, mock_video_document):
        video_with_grade = {**mock_video_document, "overall_grade": 82.4, "letter_grade": "B"}
        with patch("api.server.firestore_client") as mock_fs:
            mock_fs.get_video_status.return_value = video_with_grade
            _wire_plays(mock_fs, [{"play_index": 0, "start_frame": 0, "end_frame": 99, "status": "completed"}])

            response = client.get("/api/plays/dQw4w9WgXcQ")

        assert response.status_code == 200
        data = response.json()
        assert data["overall_grade"] == 82.4
        assert data["letter_grade"] == "B"

    @pytest.mark.endpoints
    @pytest.mark.integration
    def test_overall_grade_is_none_before_completion(self, client, mock_video_document):
        with patch("api.server.firestore_client") as mock_fs:
            mock_fs.get_video_status.return_value = mock_video_document
            _wire_plays(mock_fs, [])

            response = client.get("/api/plays/dQw4w9WgXcQ")

        data = response.json()
        assert data["overall_grade"] is None
        assert data["letter_grade"] is None

    @pytest.mark.endpoints
    @pytest.mark.integration
    def test_clip_url_present_only_when_clip_ready(self, client, mock_video_document):
        """clip_ready is set by play_detection_stage.py's inline, best-effort
        _export_play_clip - clip_url must be None (not a broken link) for any
        play where that hasn't succeeded, so the watch page's fallback to
        seeking within the full video actually gets triggered."""
        with patch("api.server.firestore_client") as mock_fs:
            mock_fs.get_video_status.return_value = mock_video_document
            _wire_plays(mock_fs, [
                {"play_index": 0, "start_frame": 0, "end_frame": 99, "status": "completed", "clip_ready": True},
                {"play_index": 1, "start_frame": 100, "end_frame": 199, "status": "completed", "clip_ready": False},
            ])

            response = client.get("/api/plays/dQw4w9WgXcQ")

        data = response.json()
        assert data["plays"][0]["clip_url"] == "/media/dQw4w9WgXcQ/plays/0.mp4"
        assert data["plays"][1]["clip_url"] is None
