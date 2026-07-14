"""
Tests for processing/box_overlay.py's pure functions: fetching stored boxes
for a track/frame-range, the defensive resolution-scale check, and building
the ffmpeg drawbox filter string (coverage threshold, gap-holding, scaling).
No FastAPI/queue mocking needed - this module is intentionally decoupled
from that layer (see api/server.py's get_rep_clip for the plumbing that
calls into it).
"""

from unittest.mock import MagicMock, patch

from processing.box_overlay import (
    MIN_COVERAGE_FRACTION,
    build_drawbox_filter,
    fetch_track_boxes,
    get_source_resolution_scale,
)


def _make_snapshot(exists, data=None):
    snap = MagicMock()
    snap.exists = exists
    snap.to_dict.return_value = data or {}
    return snap


class TestFetchTrackBoxes:
    def test_returns_boxes_keyed_by_frame_index_for_existing_docs(self):
        mock_firestore = MagicMock()
        # frame 10 has a box, frame 11 doesn't (get_all preserves ref order).
        mock_firestore.db.get_all.return_value = [
            _make_snapshot(True, {"frame_index": 10, "track_id": 7, "bbox": [1.0, 2.0, 3.0, 4.0]}),
            _make_snapshot(False),
        ]

        result = fetch_track_boxes(mock_firestore, "vid1", track_id=7, start_frame=10, end_frame=11)

        assert result == {10: [1.0, 2.0, 3.0, 4.0]}

    def test_requests_the_deterministic_doc_ids_not_a_query(self):
        mock_firestore = MagicMock()
        mock_firestore.db.get_all.return_value = []
        base_collection = mock_firestore.db.collection.return_value.document.return_value.collection.return_value

        fetch_track_boxes(mock_firestore, "vid1", track_id=7, start_frame=10, end_frame=12)

        expected_ids = ["000010_007", "000011_007", "000012_007"]
        actual_ids = [call.args[0] for call in base_collection.document.call_args_list]
        assert actual_ids == expected_ids

    def test_skips_docs_with_malformed_bbox(self):
        mock_firestore = MagicMock()
        mock_firestore.db.get_all.return_value = [
            _make_snapshot(True, {"frame_index": 10, "bbox": [1.0, 2.0]}),  # wrong length
            _make_snapshot(True, {"frame_index": 11, "bbox": None}),
        ]

        result = fetch_track_boxes(mock_firestore, "vid1", track_id=7, start_frame=10, end_frame=11)

        assert result == {}


class TestGetSourceResolutionScale:
    def test_returns_one_to_one_when_resolution_matches(self):
        mock_firestore = MagicMock()
        mock_firestore.db.collection.return_value.document.return_value.collection.return_value.document.return_value.get.return_value = (
            _make_snapshot(True, {"width": 1920, "height": 1080})
        )
        mock_extractor = MagicMock()
        mock_extractor.get_video_info.return_value = {"width": 1920, "height": 1080}

        with patch("processing.box_overlay.FFmpegFrameExtractor", return_value=mock_extractor):
            result = get_source_resolution_scale(mock_firestore, "vid1", "/tmp/video.mp4", reference_frame_index=10)

        assert result == (1.0, 1.0)

    def test_computes_scale_when_resolutions_differ(self):
        mock_firestore = MagicMock()
        mock_firestore.db.collection.return_value.document.return_value.collection.return_value.document.return_value.get.return_value = (
            _make_snapshot(True, {"width": 960, "height": 540})
        )
        mock_extractor = MagicMock()
        mock_extractor.get_video_info.return_value = {"width": 1920, "height": 1080}

        with patch("processing.box_overlay.FFmpegFrameExtractor", return_value=mock_extractor):
            result = get_source_resolution_scale(mock_firestore, "vid1", "/tmp/video.mp4", reference_frame_index=10)

        assert result == (2.0, 2.0)

    def test_returns_none_when_frame_doc_missing(self):
        mock_firestore = MagicMock()
        mock_firestore.db.collection.return_value.document.return_value.collection.return_value.document.return_value.get.return_value = (
            _make_snapshot(False)
        )

        result = get_source_resolution_scale(mock_firestore, "vid1", "/tmp/video.mp4", reference_frame_index=10)

        assert result is None

    def test_returns_none_when_ffprobe_read_fails(self):
        mock_firestore = MagicMock()
        mock_firestore.db.collection.return_value.document.return_value.collection.return_value.document.return_value.get.return_value = (
            _make_snapshot(True, {"width": 1920, "height": 1080})
        )
        mock_extractor = MagicMock()
        mock_extractor.get_video_info.return_value = None

        with patch("processing.box_overlay.FFmpegFrameExtractor", return_value=mock_extractor):
            result = get_source_resolution_scale(mock_firestore, "vid1", "/tmp/video.mp4", reference_frame_index=10)

        assert result is None

    def test_never_raises_on_unexpected_error(self):
        mock_firestore = MagicMock()
        mock_firestore.db.collection.side_effect = RuntimeError("boom")

        result = get_source_resolution_scale(mock_firestore, "vid1", "/tmp/video.mp4", reference_frame_index=10)

        assert result is None


class TestBuildDrawboxFilter:
    def test_full_coverage_produces_one_drawbox_per_frame(self):
        boxes = {i: [10.0 + i, 20.0, 30.0, 40.0] for i in range(10, 20)}

        result = build_drawbox_filter(boxes, start_frame=10, end_frame=19, fps=15.0, scale_x=1.0, scale_y=1.0)

        assert result is not None
        assert result.count("drawbox=") == 10

    def test_sparse_coverage_below_threshold_returns_none(self):
        # Only 1 of 10 frames has a box - well under MIN_COVERAGE_FRACTION.
        boxes = {10: [0.0, 0.0, 10.0, 10.0]}
        assert MIN_COVERAGE_FRACTION > 0.1

        result = build_drawbox_filter(boxes, start_frame=10, end_frame=19, fps=15.0, scale_x=1.0, scale_y=1.0)

        assert result is None

    def test_holds_last_known_box_across_an_inner_gap(self):
        # frame 15 is missing (9/10 = 0.9 coverage, at the threshold) - should
        # reuse frame 14's box for that slot rather than dropping it.
        boxes_dense = {i: [0.0, 0.0, 10.0, 10.0] for i in range(10, 20) if i != 15}

        result = build_drawbox_filter(boxes_dense, start_frame=10, end_frame=19, fps=15.0, scale_x=1.0, scale_y=1.0)

        assert result is not None
        assert result.count("drawbox=") == 10  # every frame still gets a box, including the held-over gap

    def test_applies_scale_factor_to_coordinates(self):
        boxes = {i: [10.0, 20.0, 30.0, 40.0] for i in range(10, 20)}

        result = build_drawbox_filter(boxes, start_frame=10, end_frame=19, fps=15.0, scale_x=2.0, scale_y=2.0)

        assert "x=20.0" in result
        assert "y=40.0" in result
        assert "w=40.0" in result  # (30-10)*2
        assert "h=40.0" in result  # (40-20)*2

    def test_empty_range_returns_none(self):
        result = build_drawbox_filter({}, start_frame=10, end_frame=9, fps=15.0, scale_x=1.0, scale_y=1.0)
        assert result is None
