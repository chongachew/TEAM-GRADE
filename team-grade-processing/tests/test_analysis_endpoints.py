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


def _wire_subcollections(mock_fs, analysis_docs, reps_docs=None, pose_docs=None):
    """Wire firestore_client.db.collection("videos").document(id).collection(name)
    .stream() to return analysis_docs for "analysis", reps_docs for "reps", and
    pose_docs for "pose" (the provisional-analysis preview path's source data -
    defaults to [] so tests that don't care about it don't need to know it exists)."""

    def collection_side_effect(name):
        col_mock = MagicMock()
        if name == "analysis":
            col_mock.stream.return_value = [_make_doc(d) for d in analysis_docs]
        elif name == "reps":
            col_mock.stream.return_value = [_make_doc(d) for d in (reps_docs or [])]
        elif name == "pose":
            col_mock.stream.return_value = [_make_doc(d) for d in (pose_docs or [])]
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
    def test_get_analysis_handles_null_authenticity_signals_column(self, client, mock_video_document):
        """Real 500 found live in production (2026-07-28): video_data comes
        from a real Postgres row where every column is always present as a
        dict key - a video whose authenticity_check stage never ran left
        this column genuinely NULL (not absent), so
        video_data.get("authenticity_signals", {}) returned None (the real
        stored value, not the default) and .values() on it crashed the
        whole endpoint."""
        video_with_null_signals = {**mock_video_document, "authenticity_signals": None}
        with patch("api.server.firestore_client") as mock_fs:
            mock_fs.get_video_status.return_value = video_with_null_signals
            _wire_subcollections(mock_fs, analysis_docs=[])

            response = client.get("/api/analysis/dQw4w9WgXcQ")

            assert response.status_code == 200
            data = response.json()
            assert data["authenticity_signals"] == {}
            assert data["authenticity_flagged"] is False

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
    def test_get_analysis_includes_rep_timing_and_single_athlete_sentinel(self, client, mock_video_document):
        """Single-athlete-mode videos (no track_id anywhere, the default pipeline
        path) get track_id=0 as a sentinel, and every rep is annotated with its
        start/end/duration from the reps subcollection so the frontend can seek
        the video player to it without a second request."""
        with patch("api.server.firestore_client") as mock_fs:
            mock_fs.get_video_status.return_value = mock_video_document
            _wire_subcollections(
                mock_fs,
                analysis_docs=[
                    {"rep_index": 0, "traits": {}, "buckets": {}, "overall_grade": 82.0},
                ],
                reps_docs=[
                    {"rep_index": 0, "start_frame": 30, "end_frame": 90, "duration_seconds": 4.0},
                ],
            )

            response = client.get("/api/analysis/dQw4w9WgXcQ")

            assert response.status_code == 200
            rep = response.json()["reps"][0]
            assert rep["track_id"] == 0
            assert rep["start_frame"] == 30
            assert rep["end_frame"] == 90
            assert rep["duration_seconds"] == 4.0

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
        """A video that exists but hasn't finished processing, and has no pose
        data yet either (so there's nothing to preview from), has no analysis
        docs - the caller needs to distinguish this from "video not found", so
        this is a 200 with an empty reps list, not a 404."""
        with patch("api.server.firestore_client") as mock_fs:
            mock_fs.get_video_status.return_value = mock_video_document
            _wire_subcollections(mock_fs, analysis_docs=[], pose_docs=[])

            response = client.get("/api/analysis/dQw4w9WgXcQ")

            assert response.status_code == 200
            # authenticity_flagged/authenticity_signals/provisional are additive
            # fields - always present, empty/False when there's nothing to report.
            assert response.json() == {
                "reps": [], "provisional": False,
                "authenticity_flagged": False, "authenticity_signals": {},
            }

    @pytest.mark.endpoints
    @pytest.mark.integration
    def test_get_analysis_firestore_error(self, client):
        with patch("api.server.firestore_client") as mock_fs:
            mock_fs.get_video_status.side_effect = Exception("DB Error")

            response = client.get("/api/analysis/dQw4w9WgXcQ")

            assert response.status_code == 500


class TestProvisionalAnalysis:
    """GET /api/analysis/{video_id}'s preview-while-processing path: when the
    final rep_extraction+biomechanics pass hasn't written to "analysis" yet
    but pose_stage_lite has already streamed some pose data, this runs the
    same segmentation+scoring logic on whatever's available and returns it
    as a labeled preview - see docs/team-grade-integration-blueprint.md's
    (in the-bridge.app repo) "showing plays early" plan for the full design."""

    @pytest.mark.endpoints
    @pytest.mark.integration
    def test_returns_provisional_reps_when_pose_data_exists_but_no_final_analysis(self, client, mock_video_document):
        with patch("api.server.firestore_client") as mock_fs, \
             patch("processing.rep_extraction.segment_reps_from_pose_docs") as mock_segment, \
             patch("api.server._recompute_rep_analysis") as mock_recompute:
            mock_fs.get_video_status.return_value = mock_video_document
            _wire_subcollections(mock_fs, analysis_docs=[], pose_docs=[{"frame_index": i, "landmarks": {}} for i in range(30)])
            mock_segment.return_value = (
                [{"rep_index": 0, "start_frame": 10, "end_frame": 40, "duration_frames": 31, "duration_seconds": 2.07, "track_id": None}],
                1,
            )
            mock_recompute.return_value = {
                "rep_index": 0, "track_id": None,
                "traits": {"speed": 80.0}, "buckets": {"speed": "Strong"}, "overall_grade": 80.0,
            }

            response = client.get("/api/analysis/dQw4w9WgXcQ")

            assert response.status_code == 200
            data = response.json()
            assert data["provisional"] is True
            assert len(data["reps"]) == 1
            rep = data["reps"][0]
            assert rep["track_id"] == 0  # None -> single-athlete sentinel, same convention as the final path
            assert rep["start_frame"] == 10
            assert rep["end_frame"] == 40
            assert rep["overall_grade"] == 80.0
            mock_recompute.assert_called_once_with(mock_fs, "dQw4w9WgXcQ", 0, None, 10, 40)

    @pytest.mark.endpoints
    @pytest.mark.integration
    def test_skipped_when_final_analysis_already_exists(self, client, mock_video_document):
        with patch("api.server.firestore_client") as mock_fs, \
             patch("processing.rep_extraction.segment_reps_from_pose_docs") as mock_segment:
            mock_fs.get_video_status.return_value = mock_video_document
            _wire_subcollections(
                mock_fs,
                analysis_docs=[{"rep_index": 0, "track_id": 7, "traits": {}, "buckets": {}, "overall_grade": 88.0}],
                pose_docs=[{"frame_index": 0, "landmarks": {}}],
            )

            response = client.get("/api/analysis/dQw4w9WgXcQ")

            assert response.status_code == 200
            assert response.json()["provisional"] is False
            mock_segment.assert_not_called()

    @pytest.mark.endpoints
    @pytest.mark.integration
    def test_skipped_when_video_already_completed(self, client, mock_video_document):
        completed_video = {**mock_video_document, "status": "completed"}
        with patch("api.server.firestore_client") as mock_fs, \
             patch("processing.rep_extraction.segment_reps_from_pose_docs") as mock_segment:
            mock_fs.get_video_status.return_value = completed_video
            _wire_subcollections(mock_fs, analysis_docs=[], pose_docs=[{"frame_index": 0, "landmarks": {}}])

            response = client.get("/api/analysis/dQw4w9WgXcQ")

            assert response.status_code == 200
            data = response.json()
            assert data["provisional"] is False
            assert data["reps"] == []
            mock_segment.assert_not_called()

    @pytest.mark.endpoints
    @pytest.mark.integration
    def test_falls_back_gracefully_when_provisional_computation_fails(self, client, mock_video_document):
        with patch("api.server.firestore_client") as mock_fs, \
             patch("processing.rep_extraction.segment_reps_from_pose_docs", side_effect=RuntimeError("boom")):
            mock_fs.get_video_status.return_value = mock_video_document
            _wire_subcollections(mock_fs, analysis_docs=[], pose_docs=[{"frame_index": 0, "landmarks": {}}])

            response = client.get("/api/analysis/dQw4w9WgXcQ")

            assert response.status_code == 200
            data = response.json()
            assert data["provisional"] is False
            assert data["reps"] == []
