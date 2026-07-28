"""
Integration Tests for PATCH /api/reps/{video_id} (manual play-boundary correction)

Confirms the endpoint reuses pose data already covering the whole video (no ML
re-run) to recompute a corrected rep's score, correctly resolves the
single-athlete-mode track_id=0 sentinel, and rejects out-of-bounds/too-short/
no-pose-data corrections rather than silently writing a misleading score.
"""

import pytest
from unittest.mock import MagicMock, patch


def _make_doc(data, reference=None):
    doc = MagicMock()
    doc.to_dict.return_value = data
    doc.reference = reference if reference is not None else MagicMock()
    return doc


def _make_query_mock(docs):
    """A Firestore query-like mock whose .where/.order_by return itself (so
    arbitrary chains work) and whose .stream() returns the given docs."""
    query_mock = MagicMock()
    query_mock.where.return_value = query_mock
    query_mock.order_by.return_value = query_mock
    query_mock.stream.return_value = docs
    return query_mock


def _wire_firestore(mock_fs, reps_docs, pose_docs):
    analysis_doc_mock = MagicMock()
    analysis_collection_mock = MagicMock()
    analysis_collection_mock.document.return_value = analysis_doc_mock

    reps_collection_mock = MagicMock()
    reps_collection_mock.stream.return_value = reps_docs

    def collection_side_effect(name):
        if name == "reps":
            return reps_collection_mock
        if name == "pose":
            return _make_query_mock(pose_docs)
        if name == "analysis":
            return analysis_collection_mock
        return MagicMock()

    doc_mock = MagicMock()
    doc_mock.collection.side_effect = collection_side_effect
    mock_fs.db.collection.return_value.document.return_value = doc_mock

    return analysis_doc_mock


SAMPLE_POSE_DOCS = [
    _make_doc({
        "frame_index": 20,
        "landmarks": {
            "left_ankle": {"x": 0.40, "y": 0.80, "confidence": 0.9},
            "right_ankle": {"x": 0.60, "y": 0.80, "confidence": 0.9},
            "left_hip": {"x": 0.45, "y": 0.50, "confidence": 0.9},
            "right_hip": {"x": 0.55, "y": 0.50, "confidence": 0.9},
        },
    }),
    _make_doc({
        "frame_index": 21,
        "landmarks": {
            "left_ankle": {"x": 0.42, "y": 0.78, "confidence": 0.9},
            "right_ankle": {"x": 0.62, "y": 0.78, "confidence": 0.9},
            "left_hip": {"x": 0.46, "y": 0.49, "confidence": 0.9},
            "right_hip": {"x": 0.56, "y": 0.49, "confidence": 0.9},
        },
    }),
]


class TestRepCorrectionEndpoint:
    @pytest.mark.endpoints
    @pytest.mark.integration
    def test_correct_rep_boundary_success(self, client, mock_video_document):
        mock_video_document["frame_count"] = 200
        rep_doc = _make_doc({"rep_index": 0, "track_id": 7, "start_frame": 10, "end_frame": 40})

        with patch("api.server.firestore_client") as mock_fs:
            mock_fs.get_video_status.return_value = mock_video_document
            analysis_doc_mock = _wire_firestore(mock_fs, [rep_doc], SAMPLE_POSE_DOCS)

            response = client.patch(
                "/api/reps/dQw4w9WgXcQ",
                json={"track_id": 7, "rep_index": 0, "start_frame": 20, "end_frame": 60},
            )

            assert response.status_code == 200, response.text
            data = response.json()
            assert data["rep_index"] == 0
            assert data["track_id"] == 7
            assert data["start_frame"] == 20
            assert data["end_frame"] == 60
            assert isinstance(data["overall_grade"], float)
            assert set(data["traits"].keys()) == {"strength", "agility", "speed", "technique", "awareness"}

            rep_doc.reference.update.assert_called_once()
            updated_fields = rep_doc.reference.update.call_args[0][0]
            assert updated_fields["start_frame"] == 20
            assert updated_fields["end_frame"] == 60
            assert updated_fields["duration_frames"] == 40

            analysis_doc_mock.set.assert_called_once()
            written = analysis_doc_mock.set.call_args[0][0]
            assert written["rep_index"] == 0
            assert written["track_id"] == 7

    @pytest.mark.endpoints
    @pytest.mark.integration
    def test_correct_rep_boundary_single_athlete_sentinel(self, client, mock_video_document):
        """track_id=0 in the request resolves to track_id=None on the rep doc -
        the same sentinel convention /api/analysis and /api/tracks already use."""
        mock_video_document["frame_count"] = 200
        rep_doc = _make_doc({"rep_index": 0, "start_frame": 10, "end_frame": 40})  # no track_id field

        with patch("api.server.firestore_client") as mock_fs:
            mock_fs.get_video_status.return_value = mock_video_document
            _wire_firestore(mock_fs, [rep_doc], SAMPLE_POSE_DOCS)

            response = client.patch(
                "/api/reps/dQw4w9WgXcQ",
                json={"track_id": 0, "rep_index": 0, "start_frame": 20, "end_frame": 60},
            )

            assert response.status_code == 200, response.text
            assert response.json()["track_id"] is None
            rep_doc.reference.update.assert_called_once()

    @pytest.mark.endpoints
    @pytest.mark.integration
    def test_correct_rep_boundary_out_of_bounds(self, client, mock_video_document):
        mock_video_document["frame_count"] = 50

        with patch("api.server.firestore_client") as mock_fs:
            mock_fs.get_video_status.return_value = mock_video_document

            response = client.patch(
                "/api/reps/dQw4w9WgXcQ",
                json={"track_id": 7, "rep_index": 0, "start_frame": 20, "end_frame": 60},
            )

            assert response.status_code == 400

    @pytest.mark.endpoints
    @pytest.mark.integration
    def test_correct_rep_boundary_too_short(self, client, mock_video_document):
        mock_video_document["frame_count"] = 200

        with patch("api.server.firestore_client") as mock_fs:
            mock_fs.get_video_status.return_value = mock_video_document

            response = client.patch(
                "/api/reps/dQw4w9WgXcQ",
                json={"track_id": 7, "rep_index": 0, "start_frame": 20, "end_frame": 25},
            )

            assert response.status_code == 400

    @pytest.mark.endpoints
    @pytest.mark.integration
    def test_correct_rep_boundary_disambiguates_by_play_index(self, client, mock_video_document):
        """Regression test: rep_index and track_id both reset to 0 on every
        play (Phase C), so two different plays can share the exact same
        (rep_index, track_id) pair. Without play_index in the match, a
        correction request for one play's rep could silently update a
        DIFFERENT play's rep instead. Also confirms the recomputed analysis
        doc gets play_index written onto it - omitting it meant the write
        targeted the play_index IS NULL slot instead of the real row."""
        mock_video_document["frame_count"] = 5000
        other_play_rep = _make_doc({"rep_index": 0, "track_id": 7, "play_index": 2, "start_frame": 10, "end_frame": 40})
        target_rep = _make_doc({"rep_index": 0, "track_id": 7, "play_index": 6, "start_frame": 4660, "end_frame": 4680})

        with patch("api.server.firestore_client") as mock_fs:
            mock_fs.get_video_status.return_value = mock_video_document
            analysis_doc_mock = _wire_firestore(mock_fs, [other_play_rep, target_rep], SAMPLE_POSE_DOCS)

            response = client.patch(
                "/api/reps/dQw4w9WgXcQ",
                json={"track_id": 7, "rep_index": 0, "play_index": 6, "start_frame": 20, "end_frame": 60},
            )

            assert response.status_code == 200, response.text
            # The right rep (play_index=6) got updated, not play_index=2's.
            other_play_rep.reference.update.assert_not_called()
            target_rep.reference.update.assert_called_once()

            written = analysis_doc_mock.set.call_args[0][0]
            assert written["play_index"] == 6

    @pytest.mark.endpoints
    @pytest.mark.integration
    def test_correct_rep_boundary_rep_not_found(self, client, mock_video_document):
        mock_video_document["frame_count"] = 200
        other_rep = _make_doc({"rep_index": 1, "track_id": 7, "start_frame": 10, "end_frame": 40})

        with patch("api.server.firestore_client") as mock_fs:
            mock_fs.get_video_status.return_value = mock_video_document
            _wire_firestore(mock_fs, [other_rep], SAMPLE_POSE_DOCS)

            response = client.patch(
                "/api/reps/dQw4w9WgXcQ",
                json={"track_id": 7, "rep_index": 0, "start_frame": 20, "end_frame": 60},
            )

            assert response.status_code == 404

    @pytest.mark.endpoints
    @pytest.mark.integration
    def test_correct_rep_boundary_no_pose_data_in_range(self, client, mock_video_document):
        mock_video_document["frame_count"] = 200
        rep_doc = _make_doc({"rep_index": 0, "track_id": 7, "start_frame": 10, "end_frame": 40})

        with patch("api.server.firestore_client") as mock_fs:
            mock_fs.get_video_status.return_value = mock_video_document
            rep_doc.reference.reset_mock()
            _wire_firestore(mock_fs, [rep_doc], [])  # no pose docs in the new range

            response = client.patch(
                "/api/reps/dQw4w9WgXcQ",
                json={"track_id": 7, "rep_index": 0, "start_frame": 20, "end_frame": 60},
            )

            assert response.status_code == 400
            rep_doc.reference.update.assert_not_called()

    @pytest.mark.endpoints
    @pytest.mark.integration
    def test_correct_rep_boundary_video_not_found(self, client):
        with patch("api.server.firestore_client") as mock_fs:
            mock_fs.get_video_status.return_value = None

            response = client.patch(
                "/api/reps/dQw4w9WgXcQ",
                json={"track_id": 7, "rep_index": 0, "start_frame": 20, "end_frame": 60},
            )

            assert response.status_code == 404

    @pytest.mark.endpoints
    @pytest.mark.integration
    def test_correct_rep_boundary_no_frame_count_yet(self, client, mock_video_document):
        mock_video_document.pop("frame_count", None)

        with patch("api.server.firestore_client") as mock_fs:
            mock_fs.get_video_status.return_value = mock_video_document

            response = client.patch(
                "/api/reps/dQw4w9WgXcQ",
                json={"track_id": 7, "rep_index": 0, "start_frame": 20, "end_frame": 60},
            )

            assert response.status_code == 409

    @pytest.mark.endpoints
    @pytest.mark.integration
    def test_correct_rep_boundary_invalid_video_id(self, client):
        response = client.patch(
            "/api/reps/***",
            json={"track_id": 7, "rep_index": 0, "start_frame": 20, "end_frame": 60},
        )

        assert response.status_code == 400
