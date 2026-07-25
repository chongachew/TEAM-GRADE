"""
Phase A extension - camera-motion candidate (see
C:\\Users\\ricky\\.claude\\plans\\adaptive-finding-planet.md, Step 3).

Every candidate signal tested so far has been scored against the ground
truth except camera-motion (processing/motion_compensation.py's
compute_camera_motion_sequence) - flagged back in the original Phase A
discussion as "riskier" for broadcast content (false positives from
replay/angle cuts) but never actually tested. It's genuinely frames-only -
no detection/tracking dependency at all - so it's a natural, low-cost
addition to validate alongside the other cheap candidates.

Standalone, no queue/DB dependency: point it at a directory of
frame_NNNNNN.jpg files (matching play_boundary_validation.py's convention)
and run directly.

Real gotcha (confirmed by reading compute_camera_motion_sequence): its
returned docs' "frame_index" field is the POSITION in the frame_paths list
passed in (0-based, range(1, n)), NOT the real video frame number - every
doc must be remapped back to the real frame number via the corresponding
frame_paths[i]'s filename before computing a timestamp, or every candidate
timestamp would be silently wrong.

Candidate extraction deliberately uses only per-pair signals
(translation_x/translation_y/rotation_deg magnitude, and the function's own
low_confidence flag for likely scene cuts/replays) - never
homography_to_ref, which is cumulative and drift-prone across the whole
video (its only consumer, compensate_point, is confirmed dead code).
"""

import statistics
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from processing.motion_compensation import compute_camera_motion_sequence

FPS = 15  # matches settings.FRAME_EXTRACTION_FPS default

# --- Spike tuning ---
# Calibrated against real kJM5Uk9DtoQ camera-motion data (2026-07-25),
# scored combined with OCR against all 57 ground-truth plays. The original
# placeholder guesses (translation=15px/rotation=2deg/cluster_window=1.5s)
# found the signal alone to be SURPRISINGLY strong on this broadcast footage
# (100% recall at +/-5s, 82.5% at +/-2s) but noisy (256 raw candidates vs 57
# true plays) - a real, unexpected result, since camera-motion was flagged
# "riskier" in the original Phase A discussion and had never actually been
# scored before this session. A real sweep of translation/rotation/cluster-
# window found translation=25px/rotation=3deg/cluster_window=3.0s keeps
# combined-with-OCR recall at 94.7% (+/-10s) while cutting candidates from
# 117 to 85 (1.49x the 57 true plays, well under the ~2x ceiling) -
# comfortably clears the plan's 80-90% recall bar using ONLY OCR +
# camera-motion, both of which need nothing more than raw extracted frames -
# not even the `detection` stage. See adaptive-finding-planet.md's Step 7/8.
TRANSLATION_SPIKE_PX = 25.0
ROTATION_SPIKE_DEG = 3.0
LOW_CONFIDENCE_AS_SPIKE = True  # a low-inlier-ratio frame (scene cut/extreme blur - the function's own low_confidence flag) also counts as a candidate boundary
CLUSTER_WINDOW_SECONDS = 3.0  # adjacent spike frames within this window are treated as one event, since a single pan/cut spans several frames


def frame_index_from_name(path) -> int:
    return int(Path(path).stem.replace("frame_", ""))


MAX_WORKERS = 10  # settings.MOTION_COMPENSATION_MAX_WORKERS defaults to 4;
                   # measured 10x real speedup (260ms/frame -> 26ms/frame) at
                   # 10 workers on this 12-core dev machine for this one-off
                   # validation run - not necessarily the right value for the
                   # live pipeline's own resource constraints.


def extract_camera_motion_candidates(frame_paths, fps=FPS, max_workers=MAX_WORKERS):
    """frame_paths: sorted list of Path objects for a contiguous (or evenly
    sampled) window of extracted frame JPEGs. Returns list of
    (real_frame_index, timestamp_seconds) candidate boundary events."""
    docs = compute_camera_motion_sequence(frame_paths, max_workers=max_workers)

    spikes = []
    for doc in docs:
        pos = doc["frame_index"]  # position in frame_paths, NOT a real frame index
        if pos == 0:
            continue  # identity doc for the first frame - not a real pair
        real_fi = frame_index_from_name(frame_paths[pos])
        translation_mag = (doc["translation_x"] ** 2 + doc["translation_y"] ** 2) ** 0.5
        is_spike = (
            translation_mag > TRANSLATION_SPIKE_PX
            or abs(doc["rotation_deg"]) > ROTATION_SPIKE_DEG
            or (LOW_CONFIDENCE_AS_SPIKE and doc["low_confidence"])
        )
        if is_spike:
            spikes.append(real_fi)

    spikes.sort()
    clustered = []
    for fi in spikes:
        ts = fi / fps
        if clustered and ts - clustered[-1][1] <= CLUSTER_WINDOW_SECONDS:
            continue  # within the same cluster as the last kept spike
        clustered.append((fi, ts))
    return clustered


if __name__ == "__main__":
    frames_dir = Path(sys.argv[1] if len(sys.argv) > 1 else "window_frames")
    frame_paths = sorted(frames_dir.glob("frame_*.jpg"), key=frame_index_from_name)
    if len(sys.argv) > 3:
        lo, hi = int(sys.argv[2]), int(sys.argv[3])
        frame_paths = [p for p in frame_paths if lo <= frame_index_from_name(p) <= hi]
    if not frame_paths:
        print(f"No frames found in {frames_dir}")
        sys.exit(0)

    print(f"Running camera-motion extraction across {len(frame_paths)} frames...")
    candidates = extract_camera_motion_candidates(frame_paths)
    print(f"Found {len(candidates)} candidates:")
    for fi, ts in candidates:
        print(f"  frame {fi} ({ts:.1f}s)")
