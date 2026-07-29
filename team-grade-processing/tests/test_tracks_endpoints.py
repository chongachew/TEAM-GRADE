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
                    {"track_id": 7, "play_index": 0, "jersey_number": "7", "jersey_confidence": 0.9,
                     "first_frame": 10, "last_frame": 400, "total_frames_tracked": 390,
                     "status": "active"},
                    {"track_id": 14, "play_index": 0, "jersey_number": "14", "jersey_confidence": 0.8,
                     "first_frame": 20, "last_frame": 380, "total_frames_tracked": 360,
                     "status": "active"},
                ],
            })

            response = client.get("/api/tracks/dQw4w9WgXcQ")

            assert response.status_code == 200
            tracks = response.json()["tracks"]
            assert {t["jersey_number"] for t in tracks} == {"7", "14"}
            assert all(t["instances"] for t in tracks)

    @pytest.mark.endpoints
    @pytest.mark.integration
    def test_get_tracks_groups_same_jersey_across_plays(self, client, mock_video_document):
        """The whole point of grouping: the same real player, tracked as
        track_id=0 in one play and track_id=3 in another (track_id resets
        per play - Phase C), reads the same confident jersey number in both
        and must collapse to one card with both instances, not two cards."""
        with patch("api.server.firestore_client") as mock_fs:
            mock_fs.get_video_status.return_value = mock_video_document
            _wire_collection(mock_fs, {
                "tracks_meta": [
                    {"track_id": 0, "play_index": 2, "jersey_number": "23", "jersey_confidence": 0.9,
                     "total_frames_tracked": 100},
                    {"track_id": 3, "play_index": 6, "jersey_number": "23", "jersey_confidence": 0.85,
                     "total_frames_tracked": 50},
                ],
            })

            response = client.get("/api/tracks/dQw4w9WgXcQ")

            assert response.status_code == 200
            tracks = response.json()["tracks"]
            assert len(tracks) == 1
            assert tracks[0]["jersey_number"] == "23"
            assert tracks[0]["total_frames_tracked"] == 150
            instances = {(i["track_id"], i["play_index"]) for i in tracks[0]["instances"]}
            assert instances == {(0, 2), (3, 6)}

    @pytest.mark.endpoints
    @pytest.mark.integration
    def test_get_tracks_low_confidence_jersey_stays_separate(self, client, mock_video_document):
        """A weak/unreliable jersey OCR read must never guess-merge two
        different real players into one claimable identity."""
        with patch("api.server.firestore_client") as mock_fs:
            mock_fs.get_video_status.return_value = mock_video_document
            _wire_collection(mock_fs, {
                "tracks_meta": [
                    {"track_id": 0, "play_index": 2, "jersey_number": "23", "jersey_confidence": 0.3,
                     "total_frames_tracked": 100},
                    {"track_id": 3, "play_index": 6, "jersey_number": "23", "jersey_confidence": 0.3,
                     "total_frames_tracked": 50},
                ],
            })

            response = client.get("/api/tracks/dQw4w9WgXcQ")

            assert response.status_code == 200
            tracks = response.json()["tracks"]
            assert len(tracks) == 2
            assert all(t["jersey_number"] is None for t in tracks)

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
            assert tracks[0]["instances"] == [{"track_id": 0, "play_index": None}]
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
    def test_get_thumbnail_falls_back_to_s3_when_not_local(self, client, mock_video_document, tmp_path):
        """Pass 2b data-layer migration: this API container never runs
        torso_crop_stage on its own disk (that's the separate team-grade-
        worker ECS service) - so the crop file described by Postgres's
        crop_path is fetched from S3 on demand rather than 404ing outright."""
        crop_path_str = "torso_crops/dQw4w9WgXcQ/torso_000040_007.jpg"

        def _fake_download(s3_key, local_path):
            local_path.parent.mkdir(parents=True, exist_ok=True)
            local_path.write_bytes(b"fetched from s3")
            return True

        with patch("api.server.firestore_client") as mock_fs, \
             patch("config.settings.PROJECT_ROOT", tmp_path), \
             patch("api.server.download_file", side_effect=_fake_download) as mock_download:
            mock_fs.get_video_status.return_value = mock_video_document
            _wire_collection(mock_fs, {
                "torso": [
                    {"frame_index": 40, "track_id": 7, "crop_path": crop_path_str,
                     "crop_box": [0, 0, 100, 100]},
                ],
            })

            response = client.get("/api/tracks/dQw4w9WgXcQ/thumbnail/7")

            assert response.status_code == 200
            assert response.content == b"fetched from s3"
            mock_download.assert_called_once_with(
                "videos/dQw4w9WgXcQ/torso/torso_000040_007.jpg", tmp_path / crop_path_str
            )

    @pytest.mark.endpoints
    @pytest.mark.integration
    def test_get_thumbnail_404_when_missing_locally_and_in_s3(self, client, mock_video_document, tmp_path):
        crop_path_str = "torso_crops/dQw4w9WgXcQ/torso_000040_007.jpg"

        with patch("api.server.firestore_client") as mock_fs, \
             patch("config.settings.PROJECT_ROOT", tmp_path), \
             patch("api.server.download_file", return_value=False):
            mock_fs.get_video_status.return_value = mock_video_document
            _wire_collection(mock_fs, {
                "torso": [
                    {"frame_index": 40, "track_id": 7, "crop_path": crop_path_str,
                     "crop_box": [0, 0, 100, 100]},
                ],
            })

            response = client.get("/api/tracks/dQw4w9WgXcQ/thumbnail/7")

            assert response.status_code == 404

    @pytest.mark.endpoints
    @pytest.mark.integration
    def test_get_thumbnail_disambiguates_by_play_index(self, client, mock_video_document, tmp_path):
        """Same track_id number recurring across two different plays (track_id
        resets per play - Phase C) must not mix their crops when play_index
        is provided to disambiguate."""
        wrong_play_crop = tmp_path / "wrong.jpg"
        wrong_play_crop.write_bytes(b"wrong play crop")
        right_play_crop = tmp_path / "right.jpg"
        right_play_crop.write_bytes(b"right play crop")

        with patch("api.server.firestore_client") as mock_fs, \
             patch("config.settings.PROJECT_ROOT", tmp_path):
            mock_fs.get_video_status.return_value = mock_video_document
            _wire_collection(mock_fs, {
                "torso": [
                    {"frame_index": 5, "track_id": 7, "play_index": 2, "crop_path": "wrong.jpg",
                     "crop_box": [0, 0, 100, 100]},
                    {"frame_index": 40, "track_id": 7, "play_index": 6, "crop_path": "right.jpg",
                     "crop_box": [0, 0, 10, 10]},
                ],
            })

            response = client.get("/api/tracks/dQw4w9WgXcQ/thumbnail/7?play_index=6")

            assert response.status_code == 200
            assert response.content == b"right play crop"

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
    def test_get_frame_boxes_joins_role_from_tracks_meta(self, client, mock_video_document):
        with patch("api.server.firestore_client") as mock_fs:
            mock_fs.get_video_status.return_value = mock_video_document
            _wire_collection(
                mock_fs,
                docs_by_collection={
                    "tracks_meta": [
                        {"track_id": 7, "role": "referee", "role_confidence": 0.8},
                        # track 14 has no tracks_meta row at all (older video,
                        # predates role_classification_stage) - role must be
                        # None, not a missing key or a crash.
                    ],
                },
                where_docs_by_collection={
                    "tracks": [
                        {"frame_index": 42, "track_id": 7, "bbox": [10, 20, 30, 40]},
                        {"frame_index": 42, "track_id": 14, "bbox": [50, 60, 70, 80]},
                    ],
                },
            )

            response = client.get("/api/tracks/dQw4w9WgXcQ/frame/42")

            assert response.status_code == 200
            boxes = {b["track_id"]: b["role"] for b in response.json()["boxes"]}
            assert boxes == {7: "referee", 14: None}

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
