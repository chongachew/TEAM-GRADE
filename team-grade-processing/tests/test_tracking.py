"""
Tests for DensityAwareMemoryTracker (SAM2 per-frame segmentation + hand-rolled
IoU/appearance association and selective, density-aware memory bank).

Marked slow: loads the real SAM2 model (CPU, ~seconds) - these are genuine
functional tests against real segmentation output, not mocks, since the whole
point is verifying the hand-rolled association/memory-bank logic on top of it.
Requires models/sam2.1_hiera_tiny.pt (see models/README.md).
"""

import numpy as np
import pytest

from ingest.tracking.sam2_memory_tracker import DensityAwareMemoryTracker, compute_iou

pytestmark = pytest.mark.slow

W, H = 640, 480


def _make_frame(boxes_colors, background=(34, 139, 34)):
    frame = np.full((H, W, 3), background, dtype=np.uint8)
    for box, color in boxes_colors:
        x1, y1, x2, y2 = box
        frame[y1:y2, x1:x2] = color
    return frame


@pytest.fixture(scope="module")
def tracker_factory():
    """Fresh tracker per test (tracker holds per-video state), but the
    underlying SAM2 model only needs to be usable, not re-downloaded."""
    def _make():
        return DensityAwareMemoryTracker(device="cpu")
    return _make


def test_compute_iou_basic():
    assert compute_iou([0, 0, 10, 10], [0, 0, 10, 10]) == pytest.approx(1.0)
    assert compute_iou([0, 0, 10, 10], [20, 20, 30, 30]) == 0.0
    assert 0 < compute_iou([0, 0, 10, 10], [5, 5, 15, 15]) < 1


def test_two_separate_players_get_distinct_stable_ids(tracker_factory):
    tracker = tracker_factory()
    for t in range(6):
        ax, bx = 60 + t * 20, 460 - t * 20
        box_a, box_b = [ax, 150, ax + 80, 350], [bx, 150, bx + 80, 350]
        frame_rgb = _make_frame([(box_a, (30, 30, 200)), (box_b, (200, 30, 30))])
        detections = [
            {"bbox": [float(v) for v in box_a], "confidence": 0.95},
            {"bbox": [float(v) for v in box_b], "confidence": 0.93},
        ]
        results = tracker.process_frame(t, frame_rgb, detections)
        assert len(results) == 2

    track_ids = {r["track_id"] for r in results}
    assert len(track_ids) == 2


def test_pileup_does_not_spawn_extra_tracks(tracker_factory):
    """A partial multi-frame overlap between two players shouldn't create
    more than 2 active tracks (no spurious duplicate-identity creation)."""
    tracker = tracker_factory()
    max_active = 0

    for t in range(16):
        if t < 5:
            ax, bx = 60 + t * 20, 460 - t * 20
        elif t < 10:
            ax, bx = 300, 335
        else:
            tt = t - 10
            ax, bx = 340 + tt * 20, 300 - tt * 20

        box_a, box_b = [ax, 150, ax + 80, 350], [bx, 150, bx + 80, 350]
        frame_rgb = _make_frame([(box_a, (30, 30, 200)), (box_b, (200, 30, 30))])
        detections = [
            {"bbox": [float(v) for v in box_a], "confidence": 0.95},
            {"bbox": [float(v) for v in box_b], "confidence": 0.93},
        ]
        tracker.process_frame(t, frame_rgb, detections)
        active = sum(1 for tr in tracker.tracks.values() if tr.status == "active")
        max_active = max(max_active, active)

    assert max_active == 2


def test_density_flag_fires_for_genuine_threeway_pileup(tracker_factory):
    """A box overlapping >=2 others simultaneously should be flagged occluded
    (the plan's line-of-scrimmage signature) - a single 1-on-1 overlap should not."""
    tracker = tracker_factory()

    box_1 = [280, 150, 340, 350]
    box_2 = [300, 150, 360, 350]  # overlaps both neighbors
    box_3 = [320, 150, 380, 350]
    frame_rgb = _make_frame([
        (box_1, (30, 30, 200)), (box_2, (30, 200, 30)), (box_3, (200, 30, 30)),
    ])
    detections = [
        {"bbox": [float(v) for v in box_1], "confidence": 0.95},
        {"bbox": [float(v) for v in box_2], "confidence": 0.94},
        {"bbox": [float(v) for v in box_3], "confidence": 0.93},
    ]
    results = tracker.process_frame(0, frame_rgb, detections)

    middle_result = next(r for r in results if r["bbox"] == [float(v) for v in box_2])
    assert middle_result["occlusion_state"] == "occluded"


def test_reidentification_after_genuine_absence(tracker_factory):
    """Track A disappears from detection entirely for several frames (a real
    gap, not just an overlap), then reappears at a distant position with its
    true, unblended color - the appearance-similarity path should recover the
    same track_id, not spawn a new one."""
    tracker = tracker_factory()
    box_b_fixed = [500, 150, 580, 350]  # continuously-visible reference player

    track_a_id_before = None
    track_a_result_after = None

    for t in range(15):
        boxes_colors = [(box_b_fixed, (200, 30, 30))]
        detections = []

        if t < 5:
            ax = 60 + t * 15
            box_a = [ax, 150, ax + 80, 350]
            boxes_colors.append((box_a, (30, 30, 200)))
            detections.append({"bbox": [float(v) for v in box_a], "confidence": 0.95})
        elif t < 12:
            pass  # genuinely missing - no detection at all
        else:
            ax = 400 + (t - 12) * 10
            box_a = [ax, 150, ax + 80, 350]
            boxes_colors.append((box_a, (30, 30, 200)))
            detections.append({"bbox": [float(v) for v in box_a], "confidence": 0.95})

        detections.append({"bbox": [float(v) for v in box_b_fixed], "confidence": 0.9})
        frame_rgb = _make_frame(boxes_colors)
        results = tracker.process_frame(t, frame_rgb, detections)

        a_result = next((r for r in results if r["bbox"] != [float(v) for v in box_b_fixed]), None)
        if t == 4:
            track_a_id_before = a_result["track_id"]
        if t == 12 and a_result:
            track_a_result_after = a_result

    assert track_a_id_before is not None
    assert track_a_result_after is not None
    assert track_a_result_after["track_id"] == track_a_id_before
    assert track_a_result_after["matched_via"] == "appearance_reid"
    assert tracker.tracks[track_a_id_before].status == "active"


def test_selective_memory_update_skips_near_static_frames(tracker_factory):
    """A track that doesn't move frame-to-frame shouldn't grow its memory
    bank every single frame (the mechanism behind the occlusion-degradation
    fix) - only the first observation should be banked."""
    tracker = tracker_factory()
    box = [200, 150, 280, 350]

    for t in range(5):
        frame_rgb = _make_frame([(box, (30, 30, 200))])
        detections = [{"bbox": [float(v) for v in box], "confidence": 0.95}]
        results = tracker.process_frame(t, frame_rgb, detections)

    track_id = results[0]["track_id"]
    assert len(tracker.tracks[track_id].memory_bank) == 1
