"""
Phase A validation - tracking-based snap detector (see
C:\\Users\\ricky\\.claude\\plans\\quirky-waddling-octopus.md and the
"per-play redesign" discussion). Primary candidate signal for wide
line-of-scrimmage shots: find the densest cluster of player-tracking boxes
each frame (the "pile"), measure that cluster's aggregate motion over time,
and flag a snap candidate wherever a real calm period is immediately
followed by a sharp motion burst.

Input shape (matches the `tracks` table / export_tracks.py's output):
    [{"frame_index": int, "track_id": int, "bbox": [x1, y1, x2, y2]}, ...]

Standalone, no queue/DB/pipeline dependency - takes the track list in memory
or from a JSON file. Deliberately built and unit-tested against synthetic
data first (see the __main__ block) so the core logic is verified before
ever touching real tracking data, matching how reject_replays() in
play_boundary_validation.py was validated.
"""

import json
import statistics
import sys
from collections import defaultdict

FPS = 15  # matches settings.FRAME_EXTRACTION_FPS

# --- Pile-finding tuning ---
# Calibrated against real tracking data for kJM5Uk9DtoQ (2026-07-25): the
# original placeholder guess of 3.5 found a pile in only 41% of frames
# despite a median of 9 tracked players/frame - real line-of-scrimmage
# spacing (o-line + d-line spread across several yards) is wider than that.
# A sweep of real radius values showed coverage climbing steadily up to ~8
# (71%) then plateauing (74% at 12) - picked 8 as the point past which
# widening the radius stops meaningfully helping and starts risking merging
# genuinely separate groups of players (e.g. skill players in pre-snap
# motion) into the same "pile".
PILE_RADIUS_IN_BOX_WIDTHS = 8
MIN_PILE_SIZE = 6  # fewer than this -> no reliable pile in this frame (likely a close-up), defer to the pixel-motion fallback

# --- Calm-then-burst tuning ---
CALM_FRAMES_REQUIRED = 15  # ~1s at 15fps, per the earlier discussion of what "set stance" looks like
CALM_MOTION_THRESHOLD = 0.15  # mean per-track centroid displacement, in box-widths/frame, below which counts as "calm"
BURST_MOTION_THRESHOLD = 0.6  # displacement, in box-widths/frame, above which counts as "burst"
MIN_FRAMES_BETWEEN_SNAPS = 30  # ~2s - a real snap needs a fresh calm period first; this just avoids re-triggering on the same burst's tail


def _centroid(bbox):
    x1, y1, x2, y2 = bbox
    return ((x1 + x2) / 2.0, (y1 + y2) / 2.0)


def _box_width(bbox):
    return bbox[2] - bbox[0]


def _dist(a, b):
    return ((a[0] - b[0]) ** 2 + (a[1] - b[1]) ** 2) ** 0.5


def find_pile(frame_tracks, radius_in_widths=PILE_RADIUS_IN_BOX_WIDTHS, min_pile_size=MIN_PILE_SIZE):
    """frame_tracks: list of {"track_id", "bbox"} for ONE frame.
    Returns {track_id: centroid} for the densest cluster, or None if there
    aren't enough players in the frame to identify a reliable pile (a
    close-up shot, most likely)."""
    if len(frame_tracks) < min_pile_size:
        return None

    widths = [_box_width(t["bbox"]) for t in frame_tracks]
    median_width = statistics.median(widths)
    if median_width <= 0:
        return None
    radius = radius_in_widths * median_width

    centroids = {t["track_id"]: _centroid(t["bbox"]) for t in frame_tracks}
    ids = list(centroids.keys())

    best_center_id, best_neighbors = None, []
    for cid in ids:
        c = centroids[cid]
        neighbors = [oid for oid in ids if _dist(c, centroids[oid]) <= radius]
        if len(neighbors) > len(best_neighbors):
            best_center_id, best_neighbors = cid, neighbors

    if len(best_neighbors) < min_pile_size:
        return None
    return {tid: centroids[tid] for tid in best_neighbors}


def compute_pile_motion(prev_pile, curr_pile, median_width):
    """Mean per-track centroid displacement (in box-widths) between two
    consecutive frames' piles, only over track_ids present in both -
    comparing a track's own position frame-to-frame, not conflating that
    with a different player merely entering/leaving the pile boundary."""
    if prev_pile is None or curr_pile is None or median_width <= 0:
        return None
    common_ids = set(prev_pile) & set(curr_pile)
    if not common_ids:
        return None
    displacements = [_dist(prev_pile[tid], curr_pile[tid]) / median_width for tid in common_ids]
    return sum(displacements) / len(displacements)


def detect_bursts_from_motion_series(motion_by_frame, calm_threshold, burst_threshold,
                                       calm_frames_required=CALM_FRAMES_REQUIRED,
                                       min_frames_between=MIN_FRAMES_BETWEEN_SNAPS):
    """Generic calm-then-burst detector over ANY per-frame motion-scalar
    series - shared by the tracking-based pile signal and the pixel-based
    fallback below, so both candidates use identical burst logic and only
    differ in how the motion scalar itself is computed.

    motion_by_frame: {frame_index: motion_value_or_None}, values already in
    whatever unit calm_threshold/burst_threshold are expressed in.
    Returns list of (frame_index, timestamp_seconds).
    """
    frame_indices = sorted(motion_by_frame.keys())
    calm_streak = 0
    frames_since_last = min_frames_between
    bursts = []
    prev_fi = None

    for fi in frame_indices:
        frames_since_last += 1
        motion = motion_by_frame[fi]
        contiguous = prev_fi is not None and fi - prev_fi == 1

        if motion is None or not contiguous:
            pass  # no usable measurement or a gap in coverage - doesn't move the streak either way
        elif motion <= calm_threshold:
            calm_streak += 1
        elif motion >= burst_threshold:
            if calm_streak >= calm_frames_required and frames_since_last >= min_frames_between:
                bursts.append((fi, fi / FPS))
                frames_since_last = 0
            calm_streak = 0
        else:
            calm_streak = 0  # in between calm and burst - not progress toward either

        prev_fi = fi

    return bursts


def detect_snaps(track_rows):
    """track_rows: flat list of {"frame_index", "track_id", "bbox"} across
    the whole video (or a window). Returns list of (frame_index, timestamp_seconds)
    snap candidates from the tracking-based pile signal."""
    by_frame = defaultdict(list)
    for row in track_rows:
        by_frame[row["frame_index"]].append(row)

    frame_indices = sorted(by_frame.keys())

    piles = {}
    median_widths = {}
    for fi in frame_indices:
        frame_tracks = by_frame[fi]
        piles[fi] = find_pile(frame_tracks)
        widths = [_box_width(t["bbox"]) for t in frame_tracks]
        median_widths[fi] = statistics.median(widths) if widths else 0

    motion_by_frame = {}
    prev_fi = None
    for fi in frame_indices:
        motion = None
        if prev_fi is not None and fi - prev_fi == 1:
            motion = compute_pile_motion(piles[prev_fi], piles[fi], median_widths[fi])
        motion_by_frame[fi] = motion
        prev_fi = fi

    return detect_bursts_from_motion_series(motion_by_frame, CALM_MOTION_THRESHOLD, BURST_MOTION_THRESHOLD)


def frames_with_no_pile(track_rows, min_pile_size=MIN_PILE_SIZE):
    """Frame indices where find_pile() returned None - these are exactly the
    frames the pixel-motion fallback (see pixel_motion_fallback.py) should
    cover instead, since the tracking-based signal has nothing to say about
    them (most likely a close-up, per the shot-coverage design discussion)."""
    by_frame = defaultdict(list)
    for row in track_rows:
        by_frame[row["frame_index"]].append(row)
    return sorted(fi for fi, rows in by_frame.items() if find_pile(rows, min_pile_size=min_pile_size) is None)


if __name__ == "__main__":
    if len(sys.argv) > 1:
        with open(sys.argv[1]) as f:
            track_rows = json.load(f)
        snaps = detect_snaps(track_rows)
        print(f"Found {len(snaps)} snap candidates:")
        for fi, ts in snaps:
            print(f"  frame {fi} ({ts:.1f}s)")
        sys.exit(0)

    # --- Synthetic self-test, run with no args ---
    # Simulates: 8 players in a tight pile, stationary (small jitter) for
    # 1.5s, then a sudden burst of real motion for a few frames (the snap),
    # then they scatter (large ongoing motion, not a fresh burst since no
    # calm period preceded it a second time).
    rows = []
    box_w = 40  # pixels
    base_positions = [(500 + i * 10, 400) for i in range(8)]  # 8 players in a tight line

    import random
    random.seed(0)

    frame = 0
    # Phase 1: calm pre-snap stance, 30 frames (~2s) - small jitter only
    for _ in range(30):
        for tid, (x, y) in enumerate(base_positions):
            jx, jy = random.uniform(-1, 1), random.uniform(-1, 1)  # sub-pixel jitter, well under calm threshold
            rows.append({"frame_index": frame, "track_id": tid, "bbox": [x + jx, y + jy, x + jx + box_w, y + jy + box_w]})
        frame += 1

    # Phase 2: the snap - sudden real motion for a few frames
    for step in range(5):
        for tid, (x, y) in enumerate(base_positions):
            dx = step * 25  # real displacement, well over burst threshold
            rows.append({"frame_index": frame, "track_id": tid, "bbox": [x + dx, y, x + dx + box_w, y + box_w]})
        frame += 1

    # Phase 3: scattered post-snap motion (not preceded by a fresh calm period)
    for step in range(20):
        for tid, (x, y) in enumerate(base_positions):
            dx = 125 + step * 8 + tid * 3
            rows.append({"frame_index": frame, "track_id": tid, "bbox": [x + dx, y, x + dx + box_w, y + box_w]})
        frame += 1

    snaps = detect_snaps(rows)
    print(f"Synthetic test: found {len(snaps)} snap candidate(s): {snaps}")
    assert len(snaps) == 1, f"Expected exactly 1 snap in this synthetic scenario, got {len(snaps)}"
    assert 28 <= snaps[0][0] <= 32, f"Expected the snap around frame 30 (end of calm phase), got frame {snaps[0][0]}"
    print("PASS")
