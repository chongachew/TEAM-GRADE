"""
Tests for classical (KLT + RANSAC) camera-motion estimation.
"""

import numpy as np
import cv2
import pytest

from processing.motion_compensation import estimate_homography, decompose_homography_2d


@pytest.fixture
def synthetic_frame_pair():
    """A textured base image and a version warped by a known homography,
    simulating a small camera pan + slight rotation between two frames."""
    rng = np.random.default_rng(42)
    base = rng.integers(0, 255, (480, 640), dtype=np.uint8)
    base = cv2.GaussianBlur(base, (5, 5), 0)

    angle = np.radians(2.0)
    ground_truth_W = np.array([
        [np.cos(angle), -np.sin(angle), 15.0],
        [np.sin(angle), np.cos(angle), -8.0],
        [0.0, 0.0, 1.0],
    ])
    curr = cv2.warpPerspective(base, ground_truth_W, (640, 480))
    return base, curr, ground_truth_W


def test_recovers_known_homography(synthetic_frame_pair):
    prev_gray, curr_gray, ground_truth_W = synthetic_frame_pair

    result = estimate_homography(prev_gray, curr_gray, max_corners=200, ransac_reproj_threshold=3.0)

    assert result["inlier_ratio"] > 0.9
    assert result["num_matches"] > 20

    H_to_prev = np.array(result["homography_to_prev"]).reshape(3, 3)
    W_inv = np.linalg.inv(ground_truth_W)

    H_norm = H_to_prev / H_to_prev[2, 2]
    W_inv_norm = W_inv / W_inv[2, 2]

    assert np.max(np.abs(H_norm - W_inv_norm)) < 0.01


def test_identical_frames_yield_near_identity(synthetic_frame_pair):
    prev_gray, _, _ = synthetic_frame_pair
    result = estimate_homography(prev_gray, prev_gray)
    H = np.array(result["homography_to_prev"]).reshape(3, 3)
    assert np.allclose(H, np.eye(3), atol=0.05)


def test_blank_frame_falls_back_to_identity():
    """No trackable features (e.g. a solid-color frame) shouldn't crash -
    should fall back to identity with zero matches."""
    blank_a = np.full((480, 640), 128, dtype=np.uint8)
    blank_b = np.full((480, 640), 128, dtype=np.uint8)
    result = estimate_homography(blank_a, blank_b)
    assert result["num_matches"] == 0
    H = np.array(result["homography_to_prev"]).reshape(3, 3)
    assert np.array_equal(H, np.eye(3))


def test_decompose_homography_identity_gives_zero_motion():
    tx, ty, rotation_deg, scale = decompose_homography_2d(np.eye(3))
    assert tx == 0.0
    assert ty == 0.0
    assert rotation_deg == pytest.approx(0.0, abs=0.01)
    assert scale == pytest.approx(1.0, abs=0.01)
