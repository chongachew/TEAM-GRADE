"""
Tests for classical (KLT + RANSAC) camera-motion estimation.
"""

from pathlib import Path

import numpy as np
import cv2
import pytest

from processing.motion_compensation import (
    estimate_homography,
    decompose_homography_2d,
    compute_camera_motion_sequence,
)


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


def _write_frames(tmp_path: Path, n: int, shift_per_frame: float = 4.0) -> list[Path]:
    """Writes a sequence of frames, each translated slightly further from a shared
    textured base image, simulating a slow camera pan across n frames."""
    rng = np.random.default_rng(7)
    base = rng.integers(0, 255, (240, 320), dtype=np.uint8)
    base = cv2.GaussianBlur(base, (5, 5), 0)

    paths = []
    for i in range(n):
        M = np.array([[1.0, 0.0, shift_per_frame * i], [0.0, 1.0, 0.0]])
        frame = cv2.warpAffine(base, M, (320, 240))
        path = tmp_path / f"frame_{i:05d}.jpg"
        cv2.imwrite(str(path), frame)
        paths.append(path)
    return paths


def test_compute_sequence_frame_zero_is_identity(tmp_path):
    paths = _write_frames(tmp_path, 1)
    docs = compute_camera_motion_sequence(paths)
    assert len(docs) == 1
    assert docs[0]["frame_index"] == 0
    assert np.array_equal(np.array(docs[0]["homography_to_ref"]).reshape(3, 3), np.eye(3))


def test_compute_sequence_accumulates_and_preserves_order(tmp_path):
    paths = _write_frames(tmp_path, 5)
    docs = compute_camera_motion_sequence(paths, max_workers=4)

    assert [d["frame_index"] for d in docs] == [0, 1, 2, 3, 4]
    # Cumulative translation magnitude should grow monotonically with a steady pan,
    # consistently in one direction (the sign is a convention detail, not under test).
    cumulative_tx = [
        np.array(d["homography_to_ref"]).reshape(3, 3)[0, 2] for d in docs
    ]
    assert cumulative_tx[0] == pytest.approx(0.0)
    magnitudes = [abs(v) for v in cumulative_tx]
    assert magnitudes[-1] > magnitudes[1] > 0
    assert len({np.sign(v) for v in cumulative_tx[1:]}) == 1


def test_compute_sequence_matches_regardless_of_worker_count(tmp_path):
    paths = _write_frames(tmp_path, 6)
    docs_serial = compute_camera_motion_sequence(paths, max_workers=1)
    docs_parallel = compute_camera_motion_sequence(paths, max_workers=4)

    for serial, parallel in zip(docs_serial, docs_parallel):
        assert serial["frame_index"] == parallel["frame_index"]
        assert np.allclose(serial["homography_to_ref"], parallel["homography_to_ref"])
        assert serial["inlier_ratio"] == pytest.approx(parallel["inlier_ratio"])


def test_compute_sequence_unreadable_frame_falls_back_to_identity(tmp_path):
    paths = _write_frames(tmp_path, 4)
    missing = tmp_path / "frame_00002_missing.jpg"  # never written - cv2.imread -> None
    paths[2] = missing

    docs = compute_camera_motion_sequence(paths)

    assert len(docs) == 4
    # Frame 2 itself is unreadable -> identity.
    assert np.array_equal(np.array(docs[2]["homography_to_prev"]).reshape(3, 3), np.eye(3))
    # Frame 3's pair partner (frame 2) was unreadable -> identity too, matching the
    # original sequential loop's "prev_gray reset" behavior.
    assert np.array_equal(np.array(docs[3]["homography_to_prev"]).reshape(3, 3), np.eye(3))
    # But it shouldn't have crashed or dropped a frame.
    assert [d["frame_index"] for d in docs] == [0, 1, 2, 3]


def test_compute_sequence_empty_input_returns_empty():
    assert compute_camera_motion_sequence([]) == []
