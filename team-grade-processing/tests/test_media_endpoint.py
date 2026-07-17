"""
Integration tests for GET /media/{video_id}.mp4 (Pass 2b data-layer migration).

This replaced a plain StaticFiles mount over settings.VIDEOS_DIR - that only
ever worked when the API container happened to already have the file on its
own local disk, which the worker's download stage (a separate ECS service,
separate disk) never puts there. The new route redirects to a presigned S3
URL instead (S3 natively supports the HTTP Range requests a <video> element's
scrubber needs), falling back to serving a local file directly if the video
isn't in S3 for any reason.

Mocked at the ingest.s3_client function boundary - not re-testing S3 itself
(see tests/test_s3_client.py for that).
"""

from unittest.mock import patch

import pytest


class TestGetMediaVideo:
    @pytest.mark.endpoints
    @pytest.mark.integration
    def test_redirects_to_presigned_url_when_in_s3(self, client):
        with patch("api.server.file_exists_in_s3", return_value=True), \
             patch("api.server.get_presigned_url", return_value="https://s3.example.com/signed") as mock_presign:
            response = client.get("/media/dQw4w9WgXcQ.mp4", follow_redirects=False)

        assert response.status_code == 302
        assert response.headers["location"] == "https://s3.example.com/signed"
        mock_presign.assert_called_once_with("videos/dQw4w9WgXcQ/raw.mp4")

    @pytest.mark.endpoints
    @pytest.mark.integration
    def test_falls_back_to_local_file_when_not_in_s3(self, client, tmp_path):
        video_path = tmp_path / "dQw4w9WgXcQ.mp4"
        video_path.write_bytes(b"local fallback bytes")

        with patch("api.server.file_exists_in_s3", return_value=False), \
             patch("config.settings.get_video_path", return_value=video_path):
            response = client.get("/media/dQw4w9WgXcQ.mp4")

        assert response.status_code == 200
        assert response.content == b"local fallback bytes"

    @pytest.mark.endpoints
    @pytest.mark.integration
    def test_404_when_neither_s3_nor_local(self, client, tmp_path):
        missing_path = tmp_path / "does_not_exist.mp4"

        with patch("api.server.file_exists_in_s3", return_value=False), \
             patch("config.settings.get_video_path", return_value=missing_path):
            response = client.get("/media/dQw4w9WgXcQ.mp4")

        assert response.status_code == 404

    @pytest.mark.endpoints
    @pytest.mark.integration
    def test_invalid_video_id_returns_400(self, client):
        response = client.get("/media/***.mp4")

        assert response.status_code == 400
