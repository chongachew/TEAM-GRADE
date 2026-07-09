"""
Pose-to-Track Matching (multi-player pipeline)
MediaPipe's PoseLandmarker can return multiple pose-landmark sets per frame
(num_poses > 1), but it doesn't attach any player identity to them - it's just
an unordered list per frame. This module assigns each returned landmark set to
the persistent track_id assigned by the tracking stage, via IoU between a
keypoint-derived bounding box and that frame's tracked boxes.

Deliberately NOT one MediaPipe inference call per player per frame - that would
be far more expensive (e.g. 22 players x N frames) than one multi-pose call per
frame followed by this cheap post-hoc assignment.
"""

import logging
from typing import Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)


def _keypoints_bbox(
    landmarks: Dict[str, Dict[str, float]],
    frame_width: int,
    frame_height: int,
    min_confidence: float = 0.3,
) -> Optional[List[float]]:
    """Pixel-space bounding box from a MediaPipe landmarks dict (normalized
    x/y * frame dimensions), using only keypoints above min_confidence."""
    xs, ys = [], []
    for kp in landmarks.values():
        if kp.get("confidence", 0.0) < min_confidence:
            continue
        xs.append(kp["x"] * frame_width)
        ys.append(kp["y"] * frame_height)

    if len(xs) < 3:
        return None

    return [min(xs), min(ys), max(xs), max(ys)]


def _iou(box_a: List[float], box_b: List[float]) -> float:
    ax1, ay1, ax2, ay2 = box_a
    bx1, by1, bx2, by2 = box_b

    inter_x1, inter_y1 = max(ax1, bx1), max(ay1, by1)
    inter_x2, inter_y2 = min(ax2, bx2), min(ay2, by2)
    inter_area = max(0.0, inter_x2 - inter_x1) * max(0.0, inter_y2 - inter_y1)

    area_a = max(0.0, ax2 - ax1) * max(0.0, ay2 - ay1)
    area_b = max(0.0, bx2 - bx1) * max(0.0, by2 - by1)
    union = area_a + area_b - inter_area

    return inter_area / union if union > 0 else 0.0


def match_poses_to_tracks(
    pose_landmark_sets: List[Dict[str, Dict[str, float]]],
    frame_tracks: List[Dict],
    frame_shape: Tuple[int, int, int],
    iou_threshold: float = 0.3,
) -> List[Tuple[Dict[str, Dict[str, float]], int]]:
    """Greedy one-to-one match between this frame's pose-landmark sets and this
    frame's tracked boxes (as written by the tracking stage).

    Args:
        pose_landmark_sets: One landmarks dict per pose MediaPipe returned this frame
        frame_tracks: This frame's track docs (each with a "track_id" and "bbox")
        frame_shape: (height, width, channels) of the frame
        iou_threshold: Minimum IoU to accept a pose<->track assignment

    Returns:
        List of (landmarks_dict, track_id) pairs. Poses that don't clear
        iou_threshold against any remaining track are dropped (not returned) -
        these are typically spurious MediaPipe detections that don't correspond
        to a real tracked player box.
    """
    height, width = frame_shape[0], frame_shape[1]

    candidates = []
    for pose_idx, landmarks in enumerate(pose_landmark_sets):
        bbox = _keypoints_bbox(landmarks, width, height)
        if bbox is None:
            continue
        for track in frame_tracks:
            iou = _iou(bbox, track["bbox"])
            if iou > iou_threshold:
                candidates.append((iou, pose_idx, track["track_id"]))

    # Greedy: highest-IoU pairs first, each pose and each track used at most once.
    candidates.sort(key=lambda c: c[0], reverse=True)

    assigned_poses = set()
    assigned_tracks = set()
    results: List[Tuple[Dict[str, Dict[str, float]], int]] = []

    for iou, pose_idx, track_id in candidates:
        if pose_idx in assigned_poses or track_id in assigned_tracks:
            continue
        assigned_poses.add(pose_idx)
        assigned_tracks.add(track_id)
        results.append((pose_landmark_sets[pose_idx], track_id))

    return results
