"""
Integration Tests for the Analysis Endpoint

Tests GET /api/analysis/{video_id} in api/server.py. Note: the richer analysis
surface (per-rep breakdown, summary stats, aggregated trait metrics) was
designed in api/routes/analysis_routes.py but never wired into the live app —
see README for that roadmap item. Only the single endpoint below actually
exists today.
"""

import pytest
from unittest.mock import patch


class TestAnalysisEndpoint:
    """Test GET /api/analysis/{video_id} endpoint."""

    @pytest.mark.endpoints
    @pytest.mark.integration
    def test_get_analysis_valid_video(self, client, mock_video_document):
        with patch("api.server.firestore_client") as mock_fs:
            mock_fs.get_video_status.return_value = mock_video_document

            response = client.get("/api/analysis/dQw4w9WgXcQ")

            assert response.status_code == 200
            data = response.json()
            assert data == mock_video_document["analysis"]

    @pytest.mark.endpoints
    @pytest.mark.integration
    def test_get_analysis_invalid_video_id(self, client):
        response = client.get("/api/analysis/***")

        assert response.status_code == 400

    @pytest.mark.endpoints
    @pytest.mark.integration
    def test_get_analysis_not_found(self, client):
        with patch("api.server.firestore_client") as mock_fs:
            mock_fs.get_video_status.return_value = None

            response = client.get("/api/analysis/dQw4w9WgXcQ")

            assert response.status_code == 404

    @pytest.mark.endpoints
    @pytest.mark.integration
    def test_get_analysis_no_analysis_data(self, client):
        """A video that exists but hasn't finished processing has no analysis yet."""
        with patch("api.server.firestore_client") as mock_fs:
            mock_fs.get_video_status.return_value = {"video_id": "dQw4w9WgXcQ", "status": "pending"}

            response = client.get("/api/analysis/dQw4w9WgXcQ")

            assert response.status_code == 404

    @pytest.mark.endpoints
    @pytest.mark.integration
    def test_get_analysis_firestore_error(self, client):
        with patch("api.server.firestore_client") as mock_fs:
            mock_fs.get_video_status.side_effect = Exception("DB Error")

            response = client.get("/api/analysis/dQw4w9WgXcQ")

            assert response.status_code == 500
