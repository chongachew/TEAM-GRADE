"""
Integration Tests for Ingest Endpoints

Tests POST /api/ingest and GET /api/ingest/{video_id}/status in api/server.py,
with api.server's module-level firestore_client/metadata_extractor/ingest_worker
globals mocked out.
"""

import pytest
from unittest.mock import MagicMock, patch


class TestIngestVideoEndpoint:
    """Test POST /api/ingest endpoint."""

    @pytest.mark.endpoints
    @pytest.mark.integration
    def test_ingest_fresh_video(self, client):
        """Test ingesting a video that has never been seen before."""
        with patch("api.server.metadata_extractor") as mock_meta, \
             patch("api.server.firestore_client") as mock_fs, \
             patch("api.server.ingest_worker") as mock_worker:
            mock_meta.validate_youtube_url.return_value = True
            mock_meta.get_video_id.return_value = "dQw4w9WgXcQ"
            mock_fs.check_queue_integrity.side_effect = [
                {"video_exists": False, "queue_exists": False, "status": None, "stage": None, "error": None},
                {"video_exists": True, "queue_exists": True, "status": "pending", "stage": "metadata", "error": None},
            ]
            mock_worker.ingest_youtube_url.return_value = None

            response = client.post("/api/ingest", json={
                "video_url": "https://www.youtube.com/watch?v=dQw4w9WgXcQ",
                "team_id": "team_123",
                "player_number": 42,
                "position": "rb",
            })

            assert response.status_code == 200
            data = response.json()
            assert data["video_id"] == "dQw4w9WgXcQ"
            assert data["status"] == "queued"

    @pytest.mark.endpoints
    @pytest.mark.integration
    def test_ingest_invalid_url(self, client):
        """Test ingesting a non-YouTube URL returns 400."""
        with patch("api.server.metadata_extractor") as mock_meta:
            mock_meta.validate_youtube_url.return_value = False

            response = client.post("/api/ingest", json={
                "video_url": "https://vimeo.com/123456"
            })

            assert response.status_code == 400

    @pytest.mark.endpoints
    @pytest.mark.integration
    def test_ingest_missing_video_url(self, client):
        """Test that omitting the required video_url field returns 422."""
        response = client.post("/api/ingest", json={})

        assert response.status_code == 422

    @pytest.mark.endpoints
    @pytest.mark.integration
    def test_ingest_unparseable_video_id(self, client):
        """Test a URL that passes format validation but yields no video ID returns 400."""
        with patch("api.server.metadata_extractor") as mock_meta:
            mock_meta.validate_youtube_url.return_value = True
            mock_meta.get_video_id.return_value = None

            response = client.post("/api/ingest", json={
                "video_url": "https://www.youtube.com/watch?v="
            })

            assert response.status_code == 400

    @pytest.mark.endpoints
    @pytest.mark.integration
    def test_ingest_already_processed(self, client):
        """Test that a completed video is not re-enqueued."""
        with patch("api.server.metadata_extractor") as mock_meta, \
             patch("api.server.firestore_client") as mock_fs:
            mock_meta.validate_youtube_url.return_value = True
            mock_meta.get_video_id.return_value = "dQw4w9WgXcQ"
            mock_fs.check_queue_integrity.return_value = {
                "video_exists": True, "queue_exists": True,
                "status": "completed", "stage": "complete", "error": None,
            }

            response = client.post("/api/ingest", json={
                "video_url": "https://www.youtube.com/watch?v=dQw4w9WgXcQ"
            })

            assert response.status_code == 200
            assert response.json()["status"] == "already_processed"

    @pytest.mark.endpoints
    @pytest.mark.integration
    def test_ingest_recovery_enqueue(self, client):
        """Test that a video stuck pending with no queue entry gets recovery-enqueued."""
        with patch("api.server.metadata_extractor") as mock_meta, \
             patch("api.server.firestore_client") as mock_fs, \
             patch("api.server.ingest_worker") as mock_worker:
            mock_meta.validate_youtube_url.return_value = True
            mock_meta.get_video_id.return_value = "dQw4w9WgXcQ"
            mock_fs.check_queue_integrity.side_effect = [
                {"video_exists": True, "queue_exists": False, "status": "pending", "stage": "metadata", "error": None},
                {"video_exists": True, "queue_exists": True, "status": "pending", "stage": "metadata", "error": None},
            ]
            mock_worker.ingest_youtube_url.return_value = None

            response = client.post("/api/ingest", json={
                "video_url": "https://www.youtube.com/watch?v=dQw4w9WgXcQ"
            })

            assert response.status_code == 200
            assert response.json()["status"] == "recovery_enqueue"

    @pytest.mark.endpoints
    @pytest.mark.integration
    def test_ingest_firestore_error_fails_open(self, client):
        """Test that a queue-integrity check failure defaults to attempting enqueue (fail-open)."""
        with patch("api.server.metadata_extractor") as mock_meta, \
             patch("api.server.firestore_client") as mock_fs, \
             patch("api.server.ingest_worker") as mock_worker, \
             patch("api.server.queue_manager", None):
            mock_meta.validate_youtube_url.return_value = True
            mock_meta.get_video_id.return_value = "dQw4w9WgXcQ"
            mock_fs.check_queue_integrity.side_effect = Exception("Connection failed")
            mock_worker.ingest_youtube_url.side_effect = Exception("worker unavailable")

            response = client.post("/api/ingest", json={
                "video_url": "https://www.youtube.com/watch?v=dQw4w9WgXcQ"
            })

            # Worker fails and no fallback queue manager is available -> ingestion can't succeed
            assert response.status_code == 500


class TestVideoStatusEndpoint:
    """Test GET /api/ingest/{video_id}/status endpoint."""

    @pytest.mark.endpoints
    @pytest.mark.integration
    def test_get_status_valid_video(self, client, mock_video_document):
        with patch("api.server.firestore_client") as mock_fs:
            mock_fs.get_video_status.return_value = mock_video_document

            response = client.get("/api/ingest/dQw4w9WgXcQ/status")

            assert response.status_code == 200
            data = response.json()
            assert data["video_id"] == "dQw4w9WgXcQ"
            assert "status" in data
            assert "stages" in data
            assert data["progress"] == 22  # 2 of 9 stages completed in the fixture

    @pytest.mark.endpoints
    @pytest.mark.integration
    def test_get_status_invalid_video_id(self, client):
        """An ID made entirely of characters sanitize_id strips returns 400."""
        response = client.get("/api/ingest/***/status")

        assert response.status_code == 400

    @pytest.mark.endpoints
    @pytest.mark.integration
    def test_get_status_not_found(self, client):
        with patch("api.server.firestore_client") as mock_fs:
            mock_fs.get_video_status.return_value = None

            response = client.get("/api/ingest/dQw4w9WgXcQ/status")

            assert response.status_code == 404

    @pytest.mark.endpoints
    @pytest.mark.integration
    def test_get_status_firestore_error(self, client):
        with patch("api.server.firestore_client") as mock_fs:
            mock_fs.get_video_status.side_effect = Exception("DB Error")

            response = client.get("/api/ingest/dQw4w9WgXcQ/status")

            assert response.status_code == 500
