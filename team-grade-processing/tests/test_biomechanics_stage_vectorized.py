"""
Tests for the biomechanics stage's real feature extraction.

This is the test that proves the fake-random bug is actually fixed:
_extract_pose_features used to be np.random.uniform(60, 100) for every
feature regardless of input - these tests would have failed against that
implementation (identical input would have produced different output on
every call, and different inputs would have been indistinguishable).
"""

import pytest

from ingest.stages.biomechanics_stage_vectorized import _extract_pose_features


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
