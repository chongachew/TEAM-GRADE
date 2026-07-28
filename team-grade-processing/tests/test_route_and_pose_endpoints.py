"""
Integration tests for the watch-dashboard-redesign read endpoints:
GET /api/tracks/{video_id}/route/{track_id} (player movement path) and
GET /api/pose/{video_id}/frame/{frame_index} (skeletal keypoints for one
frame) in api/server.py.
"""

import pytest
from unittest.mock import MagicMock, patch


def _make_doc(data):
    doc = MagicMock()
    doc.to_dict.return_value = data
    return doc


def _wire_chained_where(mock_fs, docs_by_collection):
    """Wire firestore_client.db.collection("videos").document(id)
    .collection(name).where(...).where(...).stream() (any number of chained
    .where()/.order_by() calls) to return canned docs for `name` - unlike
    test_tracks_endpoints.py's _wire_collection, this supports MULTIPLE
    chained .where() calls (get_track_route/get_frame_pose both optionally
    add a second .where("play_index", ...)) by making .where()/.order_by()
    return the same mock rather than a fresh unconfigured one each time."""

    def collection_side_effect(name):
        col_mock = MagicMock()
        docs = docs_by_collection.get(name, [])
        col_mock.stream.return_value = [_make_doc(d) for d in docs]
        col_mock.where.return_value = col_mock
        col_mock.order_by.return_value = col_mock
        return col_mock

    doc_mock = MagicMock()
    doc_mock.collection.side_effect = collection_side_effect
    mock_fs.db.collection.return_value.document.return_value = doc_mock


class TestTrackRouteEndpoint:
    @pytest.mark.endpoints
    @pytest.mark.integration
    def test_returns_points_ordered_by_frame_index_with_feet_position(self, client, mock_video_document):
        with patch("api.server.firestore_client") as mock_fs:
            mock_fs.get_video_status.return_value = mock_video_document
            _wire_chained_where(mock_fs, {
                "tracks": [
                    {"track_id": 7, "play_index": 2, "frame_index": 820, "bbox": [100, 200, 140, 260]},
                    {"track_id": 7, "play_index": 2, "frame_index": 810, "bbox": [90, 190, 130, 250]},
                ],
            })

            response = client.get("/api/tracks/dQw4w9WgXcQ/route/7?play_index=2")

            assert response.status_code == 200
            data = response.json()
            assert data["track_id"] == 7
            assert data["play_index"] == 2
            # Ordered by frame_index ascending, not insertion order.
            assert [p["frame_index"] for p in data["points"]] == [810, 820]
            # Point = bbox bottom-center (x midpoint, y2 - feet position).
            assert data["points"][0]["x"] == 110
            assert data["points"][0]["y"] == 250

    @pytest.mark.endpoints
    @pytest.mark.integration
    def test_skips_rows_with_missing_bbox(self, client, mock_video_document):
        with patch("api.server.firestore_client") as mock_fs:
            mock_fs.get_video_status.return_value = mock_video_document
            _wire_chained_where(mock_fs, {
                "tracks": [
                    {"track_id": 7, "play_index": 0, "frame_index": 5, "bbox": None},
                ],
            })

            response = client.get("/api/tracks/dQw4w9WgXcQ/route/7?play_index=0")

            assert response.status_code == 200
            assert response.json()["points"] == []

    @pytest.mark.endpoints
    @pytest.mark.integration
    def test_video_not_found(self, client):
        with patch("api.server.firestore_client") as mock_fs:
            mock_fs.get_video_status.return_value = None
            response = client.get("/api/tracks/dQw4w9WgXcQ/route/7")
            assert response.status_code == 404

    @pytest.mark.endpoints
    @pytest.mark.integration
    def test_invalid_video_id(self, client):
        response = client.get("/api/tracks/../../etc/route/7")
        assert response.status_code in (400, 404)


class TestFramePoseEndpoint:
    @pytest.mark.endpoints
    @pytest.mark.integration
    def test_returns_poses_for_exact_frame(self, client, mock_video_document):
        with patch("api.server.firestore_client") as mock_fs:
            mock_fs.get_video_status.return_value = mock_video_document
            _wire_chained_where(mock_fs, {
                "pose": [
                    {
                        "track_id": 7,
                        "frame_index": 812,
                        "play_index": 2,
                        "landmarks": {"nose": {"x": 0.5, "y": 0.2, "z": 0.0, "confidence": 0.9}},
                        "confidence_mean": 0.91,
                    },
                ],
            })

            response = client.get("/api/pose/dQw4w9WgXcQ/frame/812?play_index=2")

            assert response.status_code == 200
            data = response.json()
            assert data["frame_index"] == 812
            assert len(data["poses"]) == 1
            assert data["poses"][0]["track_id"] == 7
            assert data["poses"][0]["landmarks"]["nose"]["x"] == 0.5
            assert data["poses"][0]["confidence_mean"] == 0.91

    @pytest.mark.endpoints
    @pytest.mark.integration
    def test_no_poses_for_frame_returns_empty_list(self, client, mock_video_document):
        with patch("api.server.firestore_client") as mock_fs:
            mock_fs.get_video_status.return_value = mock_video_document
            _wire_chained_where(mock_fs, {"pose": []})

            response = client.get("/api/pose/dQw4w9WgXcQ/frame/1")

            assert response.status_code == 200
            assert response.json()["poses"] == []

    @pytest.mark.endpoints
    @pytest.mark.integration
    def test_video_not_found(self, client):
        with patch("api.server.firestore_client") as mock_fs:
            mock_fs.get_video_status.return_value = None
            response = client.get("/api/pose/dQw4w9WgXcQ/frame/1")
            assert response.status_code == 404
