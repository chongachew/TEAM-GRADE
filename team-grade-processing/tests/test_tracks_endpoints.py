"""
Integration Tests for the Player-Library Endpoints

Tests GET /api/tracks/{video_id}, GET /api/tracks/{video_id}/thumbnail/{track_id},
and GET /api/tracks/{video_id}/frame/{frame_index} in api/server.py. These feed the
analysis dashboard's player library and box overlay - mocks cover the tracks_meta,
torso, and tracks subcollections plus the single-athlete-mode fallback (no
tracks_meta at all, since MULTI_PLAYER_TRACKING_ENABLED defaults to false).
"""

import pytest
from unittest.mock import MagicMock, patch


def _make_doc(data, doc_id=None):
    doc = MagicMock()
    doc.to_dict.return_value = data
    if doc_id is not None:
        doc.id = doc_id
    return doc


def _wire_collection(mock_fs, docs_by_collection, where_docs_by_collection=None):
    """Wire firestore_client.db.collection("videos").document(id).collection(name)
    .stream() (and, for names in where_docs_by_collection, .where(...).stream())
    to return canned docs."""

    def collection_side_effect(name):
        col_mock = MagicMock()
        docs = docs_by_collection.get(name, [])
        col_mock.stream.return_value = [_make_doc(d, d.get("_id")) for d in docs]

        where_docs = (where_docs_by_collection or {}).get(name)
        if where_docs is not None:
            col_mock.where.return_value.stream.return_value = [
                _make_doc(d, d.get("_id")) for d in where_docs
            ]
        return col_mock

    doc_mock = MagicMock()
    doc_mock.collection.side_effect = collection_side_effect
    mock_fs.db.collection.return_value.document.return_value = doc_mock


class TestTracksEndpoint:
    """Test GET /api/tracks/{video_id} endpoint."""

    @pytest.mark.endpoints
    @pytest.mark.integration
    def test_get_tracks_multi_player_mode(self, client, mock_video_document):
        with patch("api.server.firestore_client") as mock_fs:
            mock_fs.get_video_status.return_value = mock_video_document
            _wire_collection(mock_fs, {
                "tracks_meta": [
                    {"_id": "007", "jersey_number": "7", "jersey_confidence": 0.9,
                     "first_frame": 10, "last_frame": 400, "total_frames_tracked": 390,
                     "status": "active"},
                    {"_id": "014", "jersey_number": "14", "jersey_confidence": 0.8,
                     "first_frame": 20, "last_frame": 380, "total_frames_tracked": 360,
                     "status": "active"},
                ],
            })

            response = client.get("/api/tracks/dQw4w9WgXcQ")

            assert response.status_code == 200
            tracks = response.json()["tracks"]
            assert {t["track_id"] for t in tracks} == {7, 14}
            assert all(t["jersey_number"] for t in tracks)

    @pytest.mark.endpoints
    @pytest.mark.integration
    def test_get_tracks_single_athlete_mode_fallback(self, client, mock_video_document):
        """No tracks_meta subcollection at all (the default pipeline path) still
        returns exactly one, correct card built from the root doc's jersey fields."""
        with patch("api.server.firestore_client") as mock_fs:
            mock_fs.get_video_status.return_value = mock_video_document
            _wire_collection(mock_fs, {"tracks_meta": []})

            response = client.get("/api/tracks/dQw4w9WgXcQ")

            assert response.status_code == 200
            tracks = response.json()["tracks"]
            assert len(tracks) == 1
            assert tracks[0]["track_id"] == 0
            assert tracks[0]["jersey_number"] == mock_video_document["jersey_number"]

    @pytest.mark.endpoints
    @pytest.mark.integration
    def test_get_tracks_invalid_video_id(self, client):
        response = client.get("/api/tracks/***")

        assert response.status_code == 400

    @pytest.mark.endpoints
    @pytest.mark.integration
    def test_get_tracks_video_not_found(self, client):
        with patch("api.server.firestore_client") as mock_fs:
            mock_fs.get_video_status.return_value = None

            response = client.get("/api/tracks/dQw4w9WgXcQ")

            assert response.status_code == 404

    @pytest.mark.endpoints
    @pytest.mark.integration
    def test_get_tracks_firestore_error(self, client):
        with patch("api.server.firestore_client") as mock_fs:
            mock_fs.get_video_status.side_effect = Exception("DB Error")

            response = client.get("/api/tracks/dQw4w9WgXcQ")

            assert response.status_code == 500


class TestTrackThumbnailEndpoint:
    """Test GET /api/tracks/{video_id}/thumbnail/{track_id} endpoint."""

    @pytest.mark.endpoints
    @pytest.mark.integration
    def test_get_thumbnail_picks_largest_crop_area(self, client, mock_video_document, tmp_path):
        small_crop = tmp_path / "small.jpg"
        small_crop.write_bytes(b"small crop bytes")
        big_crop = tmp_path / "big.jpg"
        big_crop.write_bytes(b"big crop bytes")

        with patch("api.server.firestore_client") as mock_fs, \
             patch("config.settings.PROJECT_ROOT", tmp_path):
            mock_fs.get_video_status.return_value = mock_video_document
            _wire_collection(mock_fs, {
                "torso": [
                    {"frame_index": 5, "track_id": 7, "crop_path": "small.jpg",
                     "crop_box": [0, 0, 10, 10]},
                    {"frame_index": 40, "track_id": 7, "crop_path": "big.jpg",
                     "crop_box": [0, 0, 100, 100]},
                ],
            })

            response = client.get("/api/tracks/dQw4w9WgXcQ/thumbnail/7")

            assert response.status_code == 200
            assert response.content == b"big crop bytes"

    @pytest.mark.endpoints
    @pytest.mark.integration
    def test_get_thumbnail_single_athlete_sentinel_matches_no_track_id_crops(
        self, client, mock_video_document, tmp_path
    ):
        crop_file = tmp_path / "solo.jpg"
        crop_file.write_bytes(b"solo athlete crop")

        with patch("api.server.firestore_client") as mock_fs, \
             patch("config.settings.PROJECT_ROOT", tmp_path):
            mock_fs.get_video_status.return_value = mock_video_document
            _wire_collection(mock_fs, {
                "torso": [
                    {"frame_index": 12, "crop_path": "solo.jpg", "crop_box": [0, 0, 50, 50]},
                ],
            })

            response = client.get("/api/tracks/dQw4w9WgXcQ/thumbnail/0")

            assert response.status_code == 200
            assert response.content == b"solo athlete crop"

    @pytest.mark.endpoints
    @pytest.mark.integration
    def test_get_thumbnail_no_crops_for_track_returns_404(self, client, mock_video_document):
        with patch("api.server.firestore_client") as mock_fs:
            mock_fs.get_video_status.return_value = mock_video_document
            _wire_collection(mock_fs, {"torso": []})

            response = client.get("/api/tracks/dQw4w9WgXcQ/thumbnail/7")

            assert response.status_code == 404

    @pytest.mark.endpoints
    @pytest.mark.integration
    def test_get_thumbnail_invalid_video_id(self, client):
        response = client.get("/api/tracks/***/thumbnail/7")

        assert response.status_code == 400

    @pytest.mark.endpoints
    @pytest.mark.integration
    def test_get_thumbnail_video_not_found(self, client):
        with patch("api.server.firestore_client") as mock_fs:
            mock_fs.get_video_status.return_value = None

            response = client.get("/api/tracks/dQw4w9WgXcQ/thumbnail/7")

            assert response.status_code == 404


class TestFrameBoxesEndpoint:
    """Test GET /api/tracks/{video_id}/frame/{frame_index} endpoint."""

    @pytest.mark.endpoints
    @pytest.mark.integration
    def test_get_frame_boxes_returns_boxes_for_exact_frame(self, client, mock_video_document):
        with patch("api.server.firestore_client") as mock_fs:
            mock_fs.get_video_status.return_value = mock_video_document
            _wire_collection(
                mock_fs,
                docs_by_collection={},
                where_docs_by_collection={
                    "tracks": [
                        {"frame_index": 42, "track_id": 7, "bbox": [10, 20, 30, 40]},
                        {"frame_index": 42, "track_id": 14, "bbox": [50, 60, 70, 80]},
                    ],
                },
            )

            response = client.get("/api/tracks/dQw4w9WgXcQ/frame/42")

            assert response.status_code == 200
            data = response.json()
            assert data["frame_index"] == 42
            assert {b["track_id"] for b in data["boxes"]} == {7, 14}

    @pytest.mark.endpoints
    @pytest.mark.integration
    def test_get_frame_boxes_invalid_video_id(self, client):
        response = client.get("/api/tracks/***/frame/42")

        assert response.status_code == 400

    @pytest.mark.endpoints
    @pytest.mark.integration
    def test_get_frame_boxes_video_not_found(self, client):
        with patch("api.server.firestore_client") as mock_fs:
            mock_fs.get_video_status.return_value = None

            response = client.get("/api/tracks/dQw4w9WgXcQ/frame/42")

            assert response.status_code == 404

    @pytest.mark.endpoints
    @pytest.mark.integration
    def test_get_frame_boxes_firestore_error(self, client):
        with patch("api.server.firestore_client") as mock_fs:
            mock_fs.get_video_status.side_effect = Exception("DB Error")

            response = client.get("/api/tracks/dQw4w9WgXcQ/frame/42")

            assert response.status_code == 500
