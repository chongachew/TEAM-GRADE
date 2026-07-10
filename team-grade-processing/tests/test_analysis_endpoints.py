"""
Integration Tests for the Analysis Endpoint

Tests GET /api/analysis/{video_id} in api/server.py. The active biomechanics stage
(biomechanics_stage_vectorized.py) writes each rep's analysis to the "analysis"
subcollection (with track_id denormalized directly into each doc), not a top-level
"analysis" field on the video document - these tests mock that subcollection query,
plus the "reps" subcollection fallback join used for any doc missing track_id.
"""

import pytest
from unittest.mock import MagicMock, patch


def _make_doc(data):
    doc = MagicMock()
    doc.to_dict.return_value = data
    return doc


def _wire_subcollections(mock_fs, analysis_docs, reps_docs=None):
    """Wire firestore_client.db.collection("videos").document(id).collection(name)
    .stream() to return analysis_docs for "analysis" and reps_docs for "reps"."""

    def collection_side_effect(name):
        col_mock = MagicMock()
        if name == "analysis":
            col_mock.stream.return_value = [_make_doc(d) for d in analysis_docs]
        elif name == "reps":
            col_mock.stream.return_value = [_make_doc(d) for d in (reps_docs or [])]
        return col_mock

    doc_mock = MagicMock()
    doc_mock.collection.side_effect = collection_side_effect
    mock_fs.db.collection.return_value.document.return_value = doc_mock


class TestAnalysisEndpoint:
    """Test GET /api/analysis/{video_id} endpoint."""

    @pytest.mark.endpoints
    @pytest.mark.integration
    def test_get_analysis_returns_reps_with_track_id(self, client, mock_video_document):
        with patch("api.server.firestore_client") as mock_fs:
            mock_fs.get_video_status.return_value = mock_video_document
            _wire_subcollections(
                mock_fs,
                analysis_docs=[
                    {"rep_index": 0, "track_id": 7, "traits": {"speed": 88.0},
                     "buckets": {"speed": "Strong"}, "overall_grade": 88.0},
                    {"rep_index": 1, "track_id": 14, "traits": {"speed": 74.0},
                     "buckets": {"speed": "Average"}, "overall_grade": 74.0},
                ],
            )

            response = client.get("/api/analysis/dQw4w9WgXcQ")

            assert response.status_code == 200
            data = response.json()
            assert len(data["reps"]) == 2
            assert {r["track_id"] for r in data["reps"]} == {7, 14}

    @pytest.mark.endpoints
    @pytest.mark.integration
    def test_get_analysis_track_id_filter(self, client, mock_video_document):
        with patch("api.server.firestore_client") as mock_fs:
            mock_fs.get_video_status.return_value = mock_video_document
            _wire_subcollections(
                mock_fs,
                analysis_docs=[
                    {"rep_index": 0, "track_id": 7, "traits": {}, "buckets": {}, "overall_grade": 88.0},
                    {"rep_index": 1, "track_id": 14, "traits": {}, "buckets": {}, "overall_grade": 74.0},
                ],
            )

            response = client.get("/api/analysis/dQw4w9WgXcQ?track_id=14")

            assert response.status_code == 200
            data = response.json()
            assert len(data["reps"]) == 1
            assert data["reps"][0]["track_id"] == 14

    @pytest.mark.endpoints
    @pytest.mark.integration
    def test_get_analysis_fallback_join_for_missing_track_id(self, client, mock_video_document):
        """Analysis docs written before track_id was denormalized into them directly
        should still resolve track_id via a fallback join against the reps collection."""
        with patch("api.server.firestore_client") as mock_fs:
            mock_fs.get_video_status.return_value = mock_video_document
            _wire_subcollections(
                mock_fs,
                analysis_docs=[
                    {"rep_index": 0, "traits": {}, "buckets": {}, "overall_grade": 88.0},
                ],
                reps_docs=[
                    {"rep_index": 0, "track_id": 7, "start_frame": 10, "end_frame": 40},
                ],
            )

            response = client.get("/api/analysis/dQw4w9WgXcQ")

            assert response.status_code == 200
            data = response.json()
            assert data["reps"][0]["track_id"] == 7

    @pytest.mark.endpoints
    @pytest.mark.integration
    def test_get_analysis_invalid_video_id(self, client):
        response = client.get("/api/analysis/***")

        assert response.status_code == 400

    @pytest.mark.endpoints
    @pytest.mark.integration
    def test_get_analysis_video_not_found(self, client):
        with patch("api.server.firestore_client") as mock_fs:
            mock_fs.get_video_status.return_value = None

            response = client.get("/api/analysis/dQw4w9WgXcQ")

            assert response.status_code == 404

    @pytest.mark.endpoints
    @pytest.mark.integration
    def test_get_analysis_no_analysis_yet_returns_empty_list_not_404(self, client, mock_video_document):
        """A video that exists but hasn't finished processing has no analysis docs
        yet - the caller needs to distinguish this from "video not found", so this
        is a 200 with an empty reps list, not a 404."""
        with patch("api.server.firestore_client") as mock_fs:
            mock_fs.get_video_status.return_value = mock_video_document
            _wire_subcollections(mock_fs, analysis_docs=[])

            response = client.get("/api/analysis/dQw4w9WgXcQ")

            assert response.status_code == 200
            assert response.json() == {"reps": []}

    @pytest.mark.endpoints
    @pytest.mark.integration
    def test_get_analysis_firestore_error(self, client):
        with patch("api.server.firestore_client") as mock_fs:
            mock_fs.get_video_status.side_effect = Exception("DB Error")

            response = client.get("/api/analysis/dQw4w9WgXcQ")

            assert response.status_code == 500
