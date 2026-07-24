"""
Phase A validation - raw-pixel motion fallback for close-up shots, where the
tracking-based pile signal (snap_detector.py) has nothing to say because too
few players are visible to identify a real line-of-scrimmage cluster.

Reuses the exact same calm-then-burst logic as the tracking-based detector
(detect_bursts_from_motion_series), just with a different motion metric:
frame-to-frame absolute pixel difference within a center-weighted crop
(close-ups are, by definition, framed on the subject - center-weighting
avoids picking up crowd/sideline motion at the edges), instead of pile
centroid displacement.

Usage: python pixel_motion_fallback.py <frames_dir> [frame_index_allowlist.json]
  frame_index_allowlist.json (optional): output of
  snap_detector.frames_with_no_pile() - restricts this fallback to exactly
  the frames the tracking signal couldn't cover, per the shot-coverage
  design (wide shots -> tracking signal; close-ups -> this fallback).
  Without it, runs across every frame in frames_dir.
"""

import json
import sys
from pathlib import Path

import cv2
import numpy as np

from snap_detector import detect_bursts_from_motion_series, FPS

# Center crop fraction - close-ups are framed on the subject, so weighting
# the center avoids false triggers from crowd movement or graphic overlays
# at the frame edges.
CENTER_CROP_FRAC = 0.5

# Pixel-difference motion is a different unit than the pile's box-width-based
# metric, so these thresholds are independent and need their own tuning pass
# once real close-up footage is available - placeholder values based on the
# same "the pre-snap stance should look nearly static" principle.
CALM_THRESHOLD = 3.0   # mean abs pixel diff (0-255 scale) below which counts as calm
BURST_THRESHOLD = 15.0  # mean abs pixel diff above which counts as a burst


def frame_index_from_name(path: Path) -> int:
    return int(path.stem.replace("frame_", ""))


def _center_crop(img):
    h, w = img.shape[:2]
    ch, cw = int(h * CENTER_CROP_FRAC), int(w * CENTER_CROP_FRAC)
    y0, x0 = (h - ch) // 2, (w - cw) // 2
    return img[y0:y0 + ch, x0:x0 + cw]


def compute_motion_series(frames_dir, allowed_indices=None):
    frame_paths = sorted(Path(frames_dir).glob("frame_*.jpg"), key=frame_index_from_name)
    if allowed_indices is not None:
        allowed = set(allowed_indices)
        frame_paths = [p for p in frame_paths if frame_index_from_name(p) in allowed]

    motion_by_frame = {}
    prev_gray = None
    prev_idx = None

    for path in frame_paths:
        idx = frame_index_from_name(path)
        img = cv2.imread(str(path))
        if img is None:
            motion_by_frame[idx] = None
            continue
        gray = cv2.cvtColor(_center_crop(img), cv2.COLOR_BGR2GRAY)

        if prev_gray is not None and idx - prev_idx == 1 and gray.shape == prev_gray.shape:
            diff = cv2.absdiff(gray, prev_gray)
            motion_by_frame[idx] = float(np.mean(diff))
        else:
            motion_by_frame[idx] = None

        prev_gray, prev_idx = gray, idx

    return motion_by_frame


def main():
    frames_dir = sys.argv[1]
    allowed_indices = None
    if len(sys.argv) > 2:
        with open(sys.argv[2]) as f:
            allowed_indices = json.load(f)

    motion_by_frame = compute_motion_series(frames_dir, allowed_indices)
    bursts = detect_bursts_from_motion_series(motion_by_frame, CALM_THRESHOLD, BURST_THRESHOLD)
    print(f"Pixel-motion fallback: {len(bursts)} candidate(s) across {len(motion_by_frame)} frames")
    for fi, ts in bursts:
        print(f"  frame {fi} ({ts:.1f}s)")


if __name__ == "__main__":
    if len(sys.argv) > 1:
        main()
        sys.exit(0)

    # --- Synthetic self-test ---
    # Simulates a calm period (near-zero pixel diff) then a sharp diff spike
    # (the burst), reusing the exact same shared burst-detection function the
    # real tracking signal uses - this test is really just confirming the
    # metric-unit wiring (calm/burst thresholds in pixel-diff units) is sane,
    # since the burst-detection logic itself is already covered by
    # snap_detector.py's own self-test.
    import random
    random.seed(1)
    motion = {}
    for i in range(30):
        motion[i] = random.uniform(0, 1.5)  # calm
    for i in range(30, 35):
        motion[i] = random.uniform(20, 30)  # burst
    for i in range(35, 55):
        motion[i] = random.uniform(10, 18)  # elevated post-snap scramble, not a fresh burst

    bursts = detect_bursts_from_motion_series(motion, CALM_THRESHOLD, BURST_THRESHOLD)
    print(f"Synthetic test: found {len(bursts)} burst(s): {bursts}")
    assert len(bursts) == 1, f"Expected exactly 1 burst, got {len(bursts)}"
    assert 28 <= bursts[0][0] <= 32, f"Expected burst around frame 30, got frame {bursts[0][0]}"
    print("PASS")
