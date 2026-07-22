"""
Camera Motion Compensation
Classical (no neural net) frame-to-frame camera-motion estimation via KLT optical
flow + RANSAC homography. Used to cancel broadcast/practice camera pans/zooms so
downstream trajectory and speed calculations reflect player motion relative to the
field, not the camera.

Design note (Phase 1 simplification): no explicit person-region mask is subtracted
from the KLT feature set before RANSAC. RANSAC's whole purpose is robust fitting in
the presence of outliers, and in a typical wide-shot broadcast/practice frame the
static background dominates the pixel count, so player motion is naturally rejected
as an outlier. This is a deliberate simplification to avoid building a second,
speculative person-detection heuristic before real footage shows whether it's
actually needed — revisit (e.g. masking out that frame's `detections` boxes before
feature selection) in Phase 2 once real occlusion-heavy footage is available to
evaluate against.
"""

import logging
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Dict, Any, List, Optional, Tuple

import cv2
import numpy as np

from config import settings
from ingest.utils.firestore_utils import get_utc_timestamp

logger = logging.getLogger(__name__)

# OpenCV's Python bindings release the GIL for the duration of each C++ call, so a
# thread pool over independent frame pairs (below) gives real parallelism without
# multiprocessing's IPC/serialization overhead. Cap OpenCV's own internal thread
# pool to 1 so it doesn't also try to parallelize each individual call - letting
# the outer ThreadPoolExecutor own the container's vCPUs instead of contending
# with OpenCV's own parallel_for backend.
cv2.setNumThreads(1)


def _identity_doc(frame_index: int, homography_to_ref: np.ndarray) -> Dict[str, Any]:
    """Build a camera_motion doc for a frame with no relative motion (frame 0, or a
    frame where estimation failed outright)."""
    return {
        "frame_index": frame_index,
        "homography_to_prev": np.eye(3).flatten().tolist(),
        "homography_to_ref": homography_to_ref.flatten().tolist(),
        "translation_x": 0.0,
        "translation_y": 0.0,
        "rotation_deg": 0.0,
        "scale": 1.0,
        "num_matches": 0,
        "num_inliers": 0,
        "inlier_ratio": 1.0,
        "low_confidence": False,
        "created_at": get_utc_timestamp(),
    }


def decompose_homography_2d(H: np.ndarray) -> Tuple[float, float, float, float]:
    """Approximate translation/rotation/scale from a homography's affine submatrix.

    Acceptable for a stabilization/trajectory-correction use case; does not attempt
    a full projective (perspective) decomposition.

    Returns:
        (translation_x, translation_y, rotation_deg, scale)
    """
    a, b = float(H[0, 0]), float(H[0, 1])
    c, d = float(H[1, 0]), float(H[1, 1])
    tx, ty = float(H[0, 2]), float(H[1, 2])
    scale = float(np.sqrt(abs(a * d - b * c)))
    rotation_deg = float(np.degrees(np.arctan2(c, a)))
    return tx, ty, rotation_deg, scale


def estimate_homography(
    prev_gray: np.ndarray,
    curr_gray: np.ndarray,
    max_corners: int = 200,
    ransac_reproj_threshold: float = 3.0,
) -> Dict[str, Any]:
    """Estimate the homography mapping curr_gray's coordinates into prev_gray's
    coordinates (i.e. "this frame -> previous frame"), via KLT + RANSAC.

    Returns a dict with homography_to_prev (flattened 3x3), num_matches, num_inliers,
    inlier_ratio. Falls back to identity if too few points/matches survive.
    """
    identity_result = {
        "homography_to_prev": np.eye(3).flatten().tolist(),
        "num_matches": 0,
        "num_inliers": 0,
        "inlier_ratio": 0.0,
    }

    prev_pts = cv2.goodFeaturesToTrack(
        prev_gray, maxCorners=max_corners, qualityLevel=0.01, minDistance=8
    )
    if prev_pts is None or len(prev_pts) < 4:
        return identity_result

    curr_pts, status, _ = cv2.calcOpticalFlowPyrLK(
        prev_gray, curr_gray, prev_pts, None, winSize=(21, 21), maxLevel=3
    )
    if curr_pts is None:
        return identity_result

    # Forward-backward error filtering: re-run LK backward and drop points whose
    # round-trip lands far from where they started — these are the ones that likely
    # drifted onto a moving player or an unreliable texture region, not the static
    # background KLT is meant to track for camera-motion estimation.
    back_pts, back_status, _ = cv2.calcOpticalFlowPyrLK(
        curr_gray, prev_gray, curr_pts, None, winSize=(21, 21), maxLevel=3
    )
    if back_pts is None:
        return identity_result

    fb_error = np.linalg.norm((prev_pts - back_pts).reshape(-1, 2), axis=1)
    good = (
        (status.reshape(-1) == 1)
        & (back_status.reshape(-1) == 1)
        & (fb_error < 1.0)
    )

    prev_good = prev_pts[good].reshape(-1, 2)
    curr_good = curr_pts[good].reshape(-1, 2)
    num_matches = len(prev_good)

    if num_matches < 4:
        return identity_result

    # curr_good as source, prev_good as destination -> H maps curr frame coords into
    # prev frame coords, i.e. exactly "homography_to_prev".
    H, inlier_mask = cv2.findHomography(
        curr_good, prev_good, cv2.RANSAC, ransac_reproj_threshold
    )
    if H is None:
        return identity_result

    num_inliers = int(inlier_mask.sum()) if inlier_mask is not None else 0
    inlier_ratio = num_inliers / num_matches if num_matches else 0.0

    return {
        "homography_to_prev": H.flatten().tolist(),
        "num_matches": num_matches,
        "num_inliers": num_inliers,
        "inlier_ratio": inlier_ratio,
    }


def _read_gray(path: Path) -> Optional[np.ndarray]:
    frame = cv2.imread(str(path))
    if frame is None:
        return None
    return cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)


def _compute_pair_motion(
    args: Tuple[int, Path, Path, int, float]
) -> Tuple[int, Optional[Dict[str, Any]]]:
    """Worker: estimate frame `idx`'s homography relative to frame `idx - 1`.

    Each pair is fully independent of every other pair, so this can run in any
    order/thread. Returns (idx, None) if either frame fails to decode - the
    caller falls back to identity for that frame, matching what the original
    sequential loop did whenever it hit an unreadable frame (its own, or the
    one immediately before it).
    """
    idx, prev_path, curr_path, max_corners, ransac_reproj_threshold = args
    prev_gray = _read_gray(prev_path)
    curr_gray = _read_gray(curr_path)
    if prev_gray is None or curr_gray is None:
        logger.debug(f"[motion_compensation] Unreadable frame in pair ending at index {idx}")
        return idx, None
    return idx, estimate_homography(prev_gray, curr_gray, max_corners, ransac_reproj_threshold)


def compute_camera_motion_sequence(
    frame_paths: List[Path],
    max_corners: Optional[int] = None,
    ransac_reproj_threshold: Optional[float] = None,
    min_inlier_ratio: Optional[float] = None,
    max_workers: Optional[int] = None,
) -> List[Dict[str, Any]]:
    """Compute per-frame camera-motion docs for an ordered sequence of frame images.

    Frame 0 gets an identity transform. Each subsequent frame gets a homography
    relative to the previous frame (homography_to_prev) and a cumulative homography
    relative to frame 0 (homography_to_ref = homography_to_ref[i-1] @ homography_to_prev[i]).

    If a frame's inlier_ratio falls below min_inlier_ratio (likely a scene cut or
    extreme motion blur), an identity transform is substituted for that frame rather
    than propagating a garbage transform into the cumulative chain, and the doc is
    flagged low_confidence=True.

    Per-pair homography estimation (the expensive KLT+RANSAC work) is farmed out
    across a thread pool since each pair is independent; only the cheap cumulative
    homography_to_ref accumulation below is inherently sequential.

    Returns:
        List of per-frame dicts matching the videos/{id}/camera_motion/{frame_index} schema.
    """
    max_corners = max_corners if max_corners is not None else settings.MOTION_COMPENSATION_MAX_CORNERS
    ransac_reproj_threshold = (
        ransac_reproj_threshold
        if ransac_reproj_threshold is not None
        else settings.MOTION_COMPENSATION_RANSAC_REPROJ_THRESHOLD
    )
    min_inlier_ratio = (
        min_inlier_ratio if min_inlier_ratio is not None else settings.MOTION_COMPENSATION_MIN_INLIER_RATIO
    )
    max_workers = max_workers if max_workers is not None else settings.MOTION_COMPENSATION_MAX_WORKERS

    n = len(frame_paths)
    if n == 0:
        return []

    pair_results: Dict[int, Optional[Dict[str, Any]]] = {}
    if n > 1:
        tasks = [
            (idx, frame_paths[idx - 1], frame_paths[idx], max_corners, ransac_reproj_threshold)
            for idx in range(1, n)
        ]
        with ThreadPoolExecutor(max_workers=max_workers) as pool:
            for idx, motion in pool.map(_compute_pair_motion, tasks):
                pair_results[idx] = motion

    docs: List[Dict[str, Any]] = []
    homography_to_ref = np.eye(3)
    docs.append(_identity_doc(0, homography_to_ref))

    for idx in range(1, n):
        motion = pair_results.get(idx)
        if motion is None:
            docs.append(_identity_doc(idx, homography_to_ref))
            continue

        low_confidence = motion["inlier_ratio"] < min_inlier_ratio
        H_to_prev = (
            np.eye(3) if low_confidence else np.array(motion["homography_to_prev"]).reshape(3, 3)
        )

        homography_to_ref = homography_to_ref @ H_to_prev
        tx, ty, rotation_deg, scale = decompose_homography_2d(H_to_prev)

        docs.append({
            "frame_index": idx,
            "homography_to_prev": H_to_prev.flatten().tolist(),
            "homography_to_ref": homography_to_ref.flatten().tolist(),
            "translation_x": tx,
            "translation_y": ty,
            "rotation_deg": rotation_deg,
            "scale": scale,
            "num_matches": motion["num_matches"],
            "num_inliers": motion["num_inliers"],
            "inlier_ratio": motion["inlier_ratio"],
            "low_confidence": low_confidence,
            "created_at": get_utc_timestamp(),
        })

    return docs


def compensate_point(x: float, y: float, homography_to_ref_flat: List[float]) -> Tuple[float, float]:
    """Map a point in this frame's pixel coordinates into the reference (frame 0)
    coordinate system, using that frame's cumulative homography_to_ref.

    Used by biomechanics feature extraction so velocity/displacement is measured
    relative to the field, not the camera.
    """
    H = np.array(homography_to_ref_flat, dtype=float).reshape(3, 3)
    pt = H @ np.array([x, y, 1.0])
    if abs(pt[2]) < 1e-9:
        return x, y
    return float(pt[0] / pt[2]), float(pt[1] / pt[2])
