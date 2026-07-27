"""
Tests for the biomechanics stage's real feature extraction.

This is the test that proves the fake-random bug is actually fixed:
_extract_pose_features used to be np.random.uniform(60, 100) for every
feature regardless of input - these tests would have failed against that
implementation (identical input would have produced different output on
every call, and different inputs would have been indistinguishable).
"""

from unittest.mock import MagicMock

import pytest

from config import settings
from unittest.mock import patch

from ingest.stages import biomechanics_stage_vectorized
from ingest.stages.biomechanics_stage_vectorized import (
    _extract_pose_features,
    run_biomechanics_stage,
)


def _make_frame(hip_x: float, ankle_x: float) -> dict:
    return {
        "left_hip": {"x": hip_x, "y": 0.5, "confidence": 0.9},
        "right_hip": {"x": hip_x + 0.05, "y": 0.5, "confidence": 0.9},
        "left_ankle": {"x": ankle_x, "y": 0.9, "confidence": 0.9},
        "right_ankle": {"x": ankle_x + 0.05, "y": 0.9, "confidence": 0.9},
        "left_shoulder": {"x": hip_x, "y": 0.3, "confidence": 0.9},
        "right_shoulder": {"x": hip_x + 0.05, "y": 0.3, "confidence": 0.9},
    }


@pytest.fixture
def high_motion_frames():
    return [_make_frame(0.3 + i * 0.03, 0.3 + i * 0.05) for i in range(10)]


@pytest.fixture
def static_frames():
    return [_make_frame(0.3, 0.3) for _ in range(10)]


def test_empty_input_returns_empty_dict():
    assert _extract_pose_features([]) == {}


def test_deterministic_on_repeated_calls(high_motion_frames):
    """The old np.random.uniform implementation would fail this: same input,
    different output every call."""
    first = _extract_pose_features(high_motion_frames)
    second = _extract_pose_features(high_motion_frames)
    assert first == second


def test_different_inputs_produce_different_outputs(high_motion_frames, static_frames):
    """The old implementation would fail this too: constant random range
    regardless of input means outputs are statistically indistinguishable."""
    high_motion = _extract_pose_features(high_motion_frames)
    static = _extract_pose_features(static_frames)
    assert high_motion != static
    assert high_motion["linear_speed"] > static["linear_speed"]


def test_static_sequence_scores_near_zero_motion(static_frames):
    features = _extract_pose_features(static_frames)
    assert features["linear_speed"] < 5.0
    assert features["acceleration"] < 5.0


def test_static_sequence_scores_high_stability(static_frames):
    features = _extract_pose_features(static_frames)
    assert features["core_stability"] > 90.0


def test_all_expected_feature_keys_present(high_motion_frames):
    features = _extract_pose_features(high_motion_frames)
    expected_keys = {
        "leg_power", "core_stability", "upper_body", "hip_mobility", "balance",
        "lateral_speed", "acceleration", "linear_speed", "deceleration",
        "footwork", "hand_placement", "body_control", "field_vision",
        "positioning", "decision_making",
    }
    assert expected_keys.issubset(features.keys())


def test_all_feature_values_are_numeric_in_0_100_range(high_motion_frames):
    """Also guards against accidentally mixing a non-numeric flag into the
    dict passed to VectorizedTraitScorer, which requires pure floats."""
    features = _extract_pose_features(high_motion_frames)
    for key, value in features.items():
        assert isinstance(value, float), f"{key} is {type(value)}, not float"
        assert 0.0 <= value <= 100.0, f"{key}={value} out of [0, 100]"


def test_single_frame_does_not_crash():
    features = _extract_pose_features([_make_frame(0.3, 0.3)])
    assert features["linear_speed"] == 0.0


class FakeDoc:
    def __init__(self, data):
        self._data = data

    def to_dict(self):
        return self._data


def _make_pose_collection_mock(pose_docs):
    """where()/order_by() return self so the real chained-call pattern
    (.where(...).where(...).order_by(...).stream()) works without needing to
    replicate per-track filtering in the mock itself."""
    m = MagicMock()
    m.where.return_value = m
    m.order_by.return_value = m
    m.stream.return_value = pose_docs
    return m


def test_writes_track_id_into_each_analysis_record():
    """Regression guard for the track_id-denormalization fix: GET
    /api/analysis/{video_id} reads from this stage's output subcollection and
    needs track_id on every doc to answer "give me this track's stats"."""
    rep_docs = [
        FakeDoc({"rep_index": 0, "track_id": 5, "start_frame": 0, "end_frame": 9}),
        FakeDoc({"rep_index": 1, "track_id": 9, "start_frame": 0, "end_frame": 9}),
    ]
    pose_docs = [
        FakeDoc({"frame_index": i, "landmarks": _make_frame(0.3 + i * 0.03, 0.3 + i * 0.05)})
        for i in range(10)
    ]

    def collection_side_effect(name):
        if name == settings.COLLECTION_REPS:
            reps_mock = MagicMock()
            reps_mock.stream.return_value = rep_docs
            return reps_mock
        if name == settings.COLLECTION_POSE:
            return _make_pose_collection_mock(pose_docs)
        return MagicMock()

    doc_mock = MagicMock()
    doc_mock.collection.side_effect = collection_side_effect

    mock_firestore = MagicMock()
    mock_firestore.db.collection.return_value.document.return_value = doc_mock
    mock_batch = MagicMock()
    mock_firestore.db.batch.return_value = mock_batch

    mock_queue = MagicMock()
    mock_queue.enqueue_video.return_value = True

    success, error = run_biomechanics_stage(mock_firestore, "dQw4w9WgXcQ", mock_queue)

    assert success is True, error
    written = [call.args[1] for call in mock_batch.set.call_args_list]
    assert len(written) == 2
    written_by_rep_index = {rep["rep_index"]: rep for rep in written}
    assert written_by_rep_index[0]["track_id"] == 5
    assert written_by_rep_index[1]["track_id"] == 9


def test_run_biomechanics_stage_scopes_to_one_play():
    rep_docs = [
        FakeDoc({"rep_index": 0, "track_id": None, "start_frame": 3, "end_frame": 5, "play_index": 1}),
    ]
    pose_docs = [
        FakeDoc({"frame_index": i, "landmarks": _make_frame(0.3 + i * 0.03, 0.3 + i * 0.05)})
        for i in (3, 4, 5)
    ]

    def collection_side_effect(name):
        if name == settings.COLLECTION_REPS:
            reps_mock = MagicMock()
            reps_mock.where.return_value.stream.return_value = rep_docs
            return reps_mock
        if name == settings.COLLECTION_POSE:
            return _make_pose_collection_mock(pose_docs)
        return MagicMock()

    doc_mock = MagicMock()
    doc_mock.collection.side_effect = collection_side_effect

    mock_firestore = MagicMock()
    mock_firestore.db.collection.return_value.document.return_value = doc_mock
    mock_batch = MagicMock()
    mock_firestore.db.batch.return_value = mock_batch

    mock_queue = MagicMock()
    mock_queue.enqueue_video.return_value = True

    with patch.object(biomechanics_stage_vectorized, "get_play", return_value={"start_frame": 3, "end_frame": 5}), \
         patch.object(biomechanics_stage_vectorized, "update_stage_status") as mock_update_status:
        success, error = run_biomechanics_stage(
            mock_firestore, "dQw4w9WgXcQ", mock_queue, payload={"play_index": 1}
        )

    assert success is True, error
    written = [call.args[1] for call in mock_batch.set.call_args_list]
    assert len(written) == 1
    assert written[0]["play_index"] == 1

    assert mock_update_status.call_args.kwargs["play_index"] == 1
    assert mock_queue.enqueue_video.call_args.kwargs["play_index"] == 1


def test_run_biomechanics_stage_missing_play_row_returns_error():
    mock_firestore = MagicMock()
    mock_queue = MagicMock()

    with patch.object(biomechanics_stage_vectorized, "get_play", return_value=None):
        success, error = run_biomechanics_stage(
            mock_firestore, "dQw4w9WgXcQ", mock_queue, payload={"play_index": 4}
        )

    assert success is False
    assert error == "PLAY_NOT_FOUND"
    mock_queue.enqueue_video.assert_not_called()
