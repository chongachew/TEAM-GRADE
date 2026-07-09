"""
Tests for assigning MediaPipe's per-frame pose-landmark sets (no identity
attached) to the tracking stage's persistent track_ids, via IoU.
"""

from processing.pose_track_matching import match_poses_to_tracks


def _make_landmarks(cx: float, cy: float, w: float = 0.1, h: float = 0.3) -> dict:
    return {
        "nose": {"x": cx, "y": cy - h / 2, "confidence": 0.9},
        "left_hip": {"x": cx - w / 2, "y": cy + h / 2, "confidence": 0.9},
        "right_hip": {"x": cx + w / 2, "y": cy + h / 2, "confidence": 0.9},
    }


def test_matches_poses_to_nearest_track_by_iou():
    pose_sets = [_make_landmarks(0.2, 0.5), _make_landmarks(0.7, 0.5)]
    frame_tracks = [
        {"track_id": 5, "bbox": [0.15 * 640, 0.35 * 480, 0.25 * 640, 0.65 * 480]},
        {"track_id": 9, "bbox": [0.65 * 640, 0.35 * 480, 0.75 * 640, 0.65 * 480]},
    ]

    matched = match_poses_to_tracks(pose_sets, frame_tracks, (480, 640, 3), iou_threshold=0.3)

    assert len(matched) == 2
    assert sorted(track_id for _, track_id in matched) == [5, 9]


def test_drops_pose_with_no_plausible_track():
    pose_sets = [_make_landmarks(0.2, 0.5), _make_landmarks(0.9, 0.9)]
    frame_tracks = [
        {"track_id": 5, "bbox": [0.15 * 640, 0.35 * 480, 0.25 * 640, 0.65 * 480]},
        {"track_id": 9, "bbox": [0.65 * 640, 0.35 * 480, 0.75 * 640, 0.65 * 480]},
    ]

    matched = match_poses_to_tracks(pose_sets, frame_tracks, (480, 640, 3), iou_threshold=0.3)

    assert len(matched) == 1
    assert matched[0][1] == 5


def test_one_to_one_assignment_no_double_matching():
    """Two poses near the same track should not both be assigned to it."""
    pose_sets = [_make_landmarks(0.2, 0.5), _make_landmarks(0.21, 0.5)]
    frame_tracks = [{"track_id": 5, "bbox": [0.15 * 640, 0.35 * 480, 0.25 * 640, 0.65 * 480]}]

    matched = match_poses_to_tracks(pose_sets, frame_tracks, (480, 640, 3), iou_threshold=0.3)

    assert len(matched) == 1


def test_empty_inputs_return_empty():
    assert match_poses_to_tracks([], [], (480, 640, 3)) == []
    assert match_poses_to_tracks([_make_landmarks(0.5, 0.5)], [], (480, 640, 3)) == []


def test_low_confidence_keypoints_ignored_for_bbox():
    """A pose with too few confident keypoints should be dropped, not crash."""
    sparse_pose = {"nose": {"x": 0.5, "y": 0.5, "confidence": 0.1}}
    frame_tracks = [{"track_id": 1, "bbox": [0, 0, 100, 100]}]
    matched = match_poses_to_tracks([sparse_pose], frame_tracks, (480, 640, 3))
    assert matched == []
