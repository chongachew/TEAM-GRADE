"""
Tests for the email-reminder feature:

- POST /api/videos/{video_id}/notify-email (api/server.py)
- complete_stage.py's fire-and-forget notification into Bridge Athletics
  once a video with a stored notify_email actually finishes.
"""

import pytest
from unittest.mock import MagicMock, patch


class TestNotifyEmailEndpoint:
    @pytest.mark.endpoints
    @pytest.mark.integration
    def test_set_notify_email_success(self, client):
        with patch("api.server.firestore_client") as mock_fs:
            mock_fs.get_video_status.return_value = {"status": "download"}
            mock_doc = MagicMock()
            mock_fs.db.collection.return_value.document.return_value = mock_doc

            response = client.post(
                "/api/videos/dQw4w9WgXcQ/notify-email",
                json={"email": "coach@example.com"},
            )

            assert response.status_code == 200
            assert response.json() == {"video_id": "dQw4w9WgXcQ", "notify_email_set": True}
            mock_doc.update.assert_called_once_with({"notify_email": "coach@example.com"})

    @pytest.mark.endpoints
    @pytest.mark.integration
    def test_set_notify_email_rejects_invalid_address(self, client):
        with patch("api.server.firestore_client") as mock_fs:
            mock_fs.get_video_status.return_value = {"status": "download"}

            response = client.post(
                "/api/videos/dQw4w9WgXcQ/notify-email",
                json={"email": "not-an-email"},
            )

            assert response.status_code == 400

    @pytest.mark.endpoints
    @pytest.mark.integration
    def test_set_notify_email_video_not_found(self, client):
        with patch("api.server.firestore_client") as mock_fs:
            mock_fs.get_video_status.return_value = None

            response = client.post(
                "/api/videos/dQw4w9WgXcQ/notify-email",
                json={"email": "coach@example.com"},
            )

            assert response.status_code == 404


class TestCompleteStageNotification:
    @pytest.mark.integration
    def test_complete_stage_notifies_when_email_set(self):
        from ingest.stages import complete_stage

        mock_fs = MagicMock()
        mock_doc = mock_fs.db.collection.return_value.document.return_value
        mock_doc.get.return_value.exists = True
        mock_doc.get.return_value.to_dict.return_value = {
            "jersey_number": "12",
            "frame_count": 100,
            "notify_email": "coach@example.com",
        }

        with patch.object(complete_stage.settings, "BRIDGE_API_BASE", "https://api.the-bridge.app"), \
             patch("ingest.stages.complete_stage.requests.post") as mock_post:
            success, error = complete_stage.run_complete_stage(mock_fs, "dQw4w9WgXcQ", queue_manager=None)

            assert success is True
            assert error is None
            mock_post.assert_called_once()
            args, kwargs = mock_post.call_args
            assert args[0] == "https://api.the-bridge.app/api/v1/video/notify-film-ready"
            assert kwargs["json"] == {"videoId": "dQw4w9WgXcQ", "email": "coach@example.com"}

    @pytest.mark.integration
    def test_complete_stage_skips_notification_when_no_email(self):
        from ingest.stages import complete_stage

        mock_fs = MagicMock()
        mock_doc = mock_fs.db.collection.return_value.document.return_value
        mock_doc.get.return_value.exists = True
        mock_doc.get.return_value.to_dict.return_value = {
            "jersey_number": "12",
            "frame_count": 100,
        }

        with patch.object(complete_stage.settings, "BRIDGE_API_BASE", "https://api.the-bridge.app"), \
             patch("ingest.stages.complete_stage.requests.post") as mock_post:
            success, error = complete_stage.run_complete_stage(mock_fs, "dQw4w9WgXcQ", queue_manager=None)

            assert success is True
            mock_post.assert_not_called()

    @pytest.mark.integration
    def test_complete_stage_notification_failure_is_non_fatal(self):
        from ingest.stages import complete_stage

        mock_fs = MagicMock()
        mock_doc = mock_fs.db.collection.return_value.document.return_value
        mock_doc.get.return_value.exists = True
        mock_doc.get.return_value.to_dict.return_value = {
            "notify_email": "coach@example.com",
        }

        with patch.object(complete_stage.settings, "BRIDGE_API_BASE", "https://api.the-bridge.app"), \
             patch("ingest.stages.complete_stage.requests.post", side_effect=Exception("boom")):
            success, error = complete_stage.run_complete_stage(mock_fs, "dQw4w9WgXcQ", queue_manager=None)

            assert success is True
            assert error is None
